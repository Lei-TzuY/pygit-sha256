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


def test_repeated_refreshes_reuse_one_client(tmp_path, monkeypatch):
    native_a = "1" * 40
    native_b = "2" * 40
    repo = _repo(tmp_path, native_a, native_b)
    created = []
    queries = []

    class FakeObjectInfoClient:
        def __init__(self, url, *, server_options=()):
            created.append((url, tuple(server_options)))

        def query_sizes(self, oids):
            queries.append(tuple(oids))
            return {oid: 7 for oid in oids}

    monkeypatch.setattr(refresh, "_OBJECT_INFO_CLIENTS", WeakKeyDictionary())
    monkeypatch.setattr(refresh, "SmartHttpV2ObjectInfoClient", FakeObjectInfoClient)

    assert refresh.refresh_promisor_sizes(repo, (native_a,)) == {native_a: 7}
    assert refresh.refresh_promisor_sizes(repo, (native_b,)) == {native_b: 7}

    assert created == [("https://example.invalid/repo.git", ())]
    assert queries == [(native_a,), (native_b,)]


def test_reused_client_keeps_native_sha1_transport_identity(tmp_path, monkeypatch):
    native_a = "a" * 40
    native_b = "B" * 40
    repo = _repo(tmp_path, native_a, native_b.lower())
    observed = []

    class FakeObjectInfoClient:
        def __init__(self, *args, **kwargs):
            pass

        def query_sizes(self, oids):
            observed.extend(oids)
            return {oid: 3 for oid in oids}

    monkeypatch.setattr(refresh, "_OBJECT_INFO_CLIENTS", WeakKeyDictionary())
    monkeypatch.setattr(refresh, "SmartHttpV2ObjectInfoClient", FakeObjectInfoClient)

    refresh.refresh_promisor_sizes(repo, (native_a,))
    refresh.refresh_promisor_sizes(repo, (native_b,))

    assert observed == [native_a, native_b.lower()]
    assert all(len(oid) == 40 for oid in observed)
    assert all(set(oid) <= set("0123456789abcdef") for oid in observed)


def test_empty_or_fully_persisted_refresh_does_not_create_cached_client(tmp_path, monkeypatch):
    native = "c" * 40
    repo = _repo(tmp_path, native)
    update_promisor_state(repo.pygit_dir, sizes={native: 9})
    constructed = []

    class UnexpectedClient:
        def __init__(self, *args, **kwargs):
            constructed.append(True)

    cache = WeakKeyDictionary()
    monkeypatch.setattr(refresh, "_OBJECT_INFO_CLIENTS", cache)
    monkeypatch.setattr(refresh, "SmartHttpV2ObjectInfoClient", UnexpectedClient)

    assert refresh.refresh_promisor_sizes(repo, ()) == {}
    assert refresh.refresh_promisor_sizes(repo, (native,)) == {native: 9}
    assert constructed == []
    assert len(cache) == 0


def test_effective_remote_configuration_is_part_of_cache_key(tmp_path, monkeypatch):
    native_a = "d" * 40
    native_b = "e" * 40
    repo = _repo(tmp_path, native_a, native_b)
    created = []
    options = [()]

    class FakeObjectInfoClient:
        def __init__(self, url, *, server_options=()):
            created.append((url, tuple(server_options)))

        def query_sizes(self, oids):
            return {oid: 5 for oid in oids}

    monkeypatch.setattr(refresh, "_OBJECT_INFO_CLIENTS", WeakKeyDictionary())
    monkeypatch.setattr(refresh, "SmartHttpV2ObjectInfoClient", FakeObjectInfoClient)
    monkeypatch.setattr(refresh, "configured_server_options", lambda repo, remote: options[0])

    refresh.refresh_promisor_sizes(repo, (native_a,))
    options[0] = ("trace=1",)
    refresh.refresh_promisor_sizes(repo, (native_b,))

    assert created == [
        ("https://example.invalid/repo.git", ()),
        ("https://example.invalid/repo.git", ("trace=1",)),
    ]
