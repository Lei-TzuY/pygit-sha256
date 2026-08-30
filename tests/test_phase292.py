from __future__ import annotations

import gc
import time
from threading import Barrier, Event, Thread
from weakref import WeakKeyDictionary, ref

from pygit import promisor_size_refresh as refresh
from pygit.promisor import update_promisor_state
from pygit.repo import Repository


def _repo(tmp_path, name: str, *native_oids: str):
    repo = Repository.init(str(tmp_path / name))
    repo.add_remote("origin", "https://example.invalid/repo.git")
    update_promisor_state(
        repo.pygit_dir,
        remote="origin",
        filter_spec="blob:none",
        promised={oid: "blob" for oid in native_oids},
    )
    return repo


def _reset_caches(monkeypatch):
    monkeypatch.setattr(refresh, "_OBJECT_INFO_CLIENTS", WeakKeyDictionary())
    monkeypatch.setattr(refresh, "_PROMISOR_REFRESH_LOCKS", WeakKeyDictionary())


def test_same_repository_refresh_waits_for_repository_lock(tmp_path, monkeypatch):
    native = "1" * 40
    repo = _repo(tmp_path, "repo", native)
    created = []
    normalized = Event()
    done = Event()
    result = []

    class FakeObjectInfoClient:
        def __init__(self, *args, **kwargs):
            created.append(True)

        def query_sizes(self, oids):
            return {oid: 17 for oid in oids}

    _reset_caches(monkeypatch)
    monkeypatch.setattr(refresh, "SmartHttpV2ObjectInfoClient", FakeObjectInfoClient)
    original_normalize = refresh._normalize_native_oids

    def observe_normalize(oids):
        value = original_normalize(oids)
        normalized.set()
        return value

    monkeypatch.setattr(refresh, "_normalize_native_oids", observe_normalize)

    def run_refresh():
        result.append(refresh.refresh_promisor_sizes(repo, (native,)))
        done.set()

    repository_lock = refresh._repo_refresh_lock(repo)
    worker = Thread(target=run_refresh)
    with repository_lock:
        worker.start()
        assert normalized.wait(1)
        assert not done.wait(0.1)
        assert created == []

    worker.join(timeout=1)
    assert not worker.is_alive()
    assert result == [{native: 17}]
    assert created == [True]


def test_different_repositories_refresh_while_other_repo_is_locked(tmp_path, monkeypatch):
    native_a = "2" * 40
    native_b = "3" * 40
    repo_a = _repo(tmp_path, "repo-a", native_a)
    repo_b = _repo(tmp_path, "repo-b", native_b)
    done = Event()
    result = []

    class FakeObjectInfoClient:
        def __init__(self, *args, **kwargs):
            pass

        def query_sizes(self, oids):
            return {oid: 23 for oid in oids}

    _reset_caches(monkeypatch)
    monkeypatch.setattr(refresh, "SmartHttpV2ObjectInfoClient", FakeObjectInfoClient)

    def refresh_b():
        result.append(refresh.refresh_promisor_sizes(repo_b, (native_b,)))
        done.set()

    worker = Thread(target=refresh_b)
    with refresh._repo_refresh_lock(repo_a):
        worker.start()
        assert done.wait(1)

    worker.join(timeout=1)
    assert not worker.is_alive()
    assert result == [{native_b: 23}]


def test_concurrent_client_lookup_constructs_one_client_per_cache_key(
    tmp_path, monkeypatch
):
    native = "4" * 40
    repo = _repo(tmp_path, "repo", native)
    created = []
    returned = []
    workers = 8
    start = Barrier(workers)

    class FakeObjectInfoClient:
        def __init__(self, *args, **kwargs):
            time.sleep(0.02)
            created.append(self)

    _reset_caches(monkeypatch)
    monkeypatch.setattr(refresh, "SmartHttpV2ObjectInfoClient", FakeObjectInfoClient)

    def lookup():
        start.wait()
        returned.append(
            refresh._object_info_client(
                repo,
                "origin",
                "https://example.invalid/repo.git",
                (),
            )
        )

    threads = [Thread(target=lookup) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()

    assert len(created) == 1
    assert len(returned) == workers
    assert all(client is created[0] for client in returned)


def test_unsupported_capability_client_survives_serialized_refreshes(
    tmp_path, monkeypatch
):
    native = "5" * 40
    repo = _repo(tmp_path, "repo", native)
    created = []
    queries = []

    class FakeObjectInfoClient:
        def __init__(self, *args, **kwargs):
            created.append(self)

        def query_sizes(self, oids):
            queries.append(tuple(oids))
            raise refresh.ObjectInfoUnsupportedError(
                "Remote protocol-v2 server does not advertise object-info"
            )

    _reset_caches(monkeypatch)
    monkeypatch.setattr(refresh, "SmartHttpV2ObjectInfoClient", FakeObjectInfoClient)

    assert refresh.refresh_promisor_sizes(repo, (native,)) == {}
    assert refresh.refresh_promisor_sizes(repo, (native,)) == {}

    assert len(created) == 1
    assert queries == [(native,), (native,)]


def test_repository_refresh_lock_cache_does_not_keep_repository_alive(
    tmp_path, monkeypatch
):
    native = "6" * 40
    lock_cache = WeakKeyDictionary()
    monkeypatch.setattr(refresh, "_PROMISOR_REFRESH_LOCKS", lock_cache)

    def create_repository_reference():
        repo = _repo(tmp_path, "repo", native)
        refresh._repo_refresh_lock(repo)
        assert len(lock_cache) == 1
        return ref(repo)

    repository_ref = create_repository_reference()
    gc.collect()

    assert repository_ref() is None
    assert len(lock_cache) == 0
