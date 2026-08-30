from __future__ import annotations

from weakref import WeakKeyDictionary

from pygit import promisor_size_refresh as refresh
from pygit.promisor import update_promisor_state
from pygit.repo import Repository


def _repo(tmp_path, *native_oids: str):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.invalid/repo.git")
    update_promisor_state(
        repo.pygit_dir,
        remote="origin",
        filter_spec="blob:none",
        promised={oid: "blob" for oid in native_oids},
    )
    return repo


def test_failed_reused_client_is_replaced_on_later_refresh(tmp_path, monkeypatch):
    native = "1" * 40
    repo = _repo(tmp_path, native)
    created = []
    queries = []

    class FakeObjectInfoClient:
        def __init__(self, url, *, server_options=()):
            self.generation = len(created)
            created.append((url, tuple(server_options)))

        def query_sizes(self, oids):
            queries.append((self.generation, tuple(oids)))
            if self.generation == 0:
                raise OSError("stale connection")
            return {oid: 17 for oid in oids}

    monkeypatch.setattr(refresh, "_OBJECT_INFO_CLIENTS", WeakKeyDictionary())
    monkeypatch.setattr(refresh, "SmartHttpV2ObjectInfoClient", FakeObjectInfoClient)

    assert refresh.refresh_promisor_sizes(repo, (native,)) == {}
    assert refresh.refresh_promisor_sizes(repo, (native,)) == {native: 17}

    assert created == [
        ("https://example.invalid/repo.git", ()),
        ("https://example.invalid/repo.git", ()),
    ]
    assert queries == [(0, (native,)), (1, (native,))]


def test_failed_client_is_not_retried_again_within_same_remote_pass(tmp_path, monkeypatch):
    native_a = "2" * 40
    native_b = "3" * 40
    repo = _repo(tmp_path, native_a, native_b)
    created = []
    queries = []

    class FakeObjectInfoClient:
        def __init__(self, *args, **kwargs):
            created.append(True)

        def query_sizes(self, oids):
            queries.append(tuple(oids))
            raise RuntimeError("protocol session failed")

    monkeypatch.setattr(refresh, "OBJECT_INFO_SIZE_BATCH", 1)
    monkeypatch.setattr(refresh, "_OBJECT_INFO_CLIENTS", WeakKeyDictionary())
    monkeypatch.setattr(refresh, "SmartHttpV2ObjectInfoClient", FakeObjectInfoClient)

    assert refresh.refresh_promisor_sizes(repo, (native_a, native_b)) == {}

    assert created == [True]
    assert queries == [(native_a,)]


def test_evict_is_identity_guarded_against_newer_replacement(tmp_path, monkeypatch):
    native = "4" * 40
    repo = _repo(tmp_path, native)

    class FakeObjectInfoClient:
        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr(refresh, "_OBJECT_INFO_CLIENTS", WeakKeyDictionary())
    monkeypatch.setattr(refresh, "SmartHttpV2ObjectInfoClient", FakeObjectInfoClient)

    old_client = refresh._object_info_client(repo, "origin", "https://example.invalid/repo.git", ())
    cache = refresh._OBJECT_INFO_CLIENTS[repo]
    key = refresh._client_cache_key("origin", "https://example.invalid/repo.git", ())
    newer_client = FakeObjectInfoClient()
    cache[key] = newer_client

    refresh._evict_object_info_client(
        repo,
        "origin",
        "https://example.invalid/repo.git",
        (),
        old_client,
    )

    assert refresh._OBJECT_INFO_CLIENTS[repo][key] is newer_client
