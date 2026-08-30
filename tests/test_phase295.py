from __future__ import annotations

from weakref import WeakKeyDictionary

from pygit import promisor_size_refresh as refresh
from pygit.promisor import update_promisor_state
from pygit.protocol_v2_object_info import parse_object_info_size_response
from pygit.remote import pkt_line
from pygit.repo import Repository


def _repo(tmp_path, *native_oids: str):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.invalid/origin.git")
    update_promisor_state(
        repo.pygit_dir,
        remote="origin",
        filter_spec="blob:none",
        promised={oid: "blob" for oid in native_oids},
    )
    return repo


def _reset_client_cache(monkeypatch):
    monkeypatch.setattr(refresh, "_OBJECT_INFO_CLIENTS", WeakKeyDictionary())


def _cached_keys(repo):
    with refresh._OBJECT_INFO_CACHE_GUARD:
        return tuple(refresh._OBJECT_INFO_CLIENTS.get(repo, {}))


def test_changed_remote_url_prunes_old_client_before_replacement(tmp_path, monkeypatch):
    native_a = "1" * 40
    native_b = "2" * 40
    repo = _repo(tmp_path, native_a, native_b)
    created = []

    class FakeObjectInfoClient:
        def __init__(self, url, *, server_options=()):
            self.url = url
            created.append((url, tuple(server_options)))

        def query_sizes(self, oids):
            return {oid: 7 for oid in oids}

    _reset_client_cache(monkeypatch)
    monkeypatch.setattr(refresh, "SmartHttpV2ObjectInfoClient", FakeObjectInfoClient)

    assert refresh.refresh_promisor_sizes(repo, (native_a,)) == {native_a: 7}
    repo.add_remote("origin", "https://example.invalid/new-origin.git")
    assert refresh.refresh_promisor_sizes(repo, (native_b,)) == {native_b: 7}

    assert created == [
        ("https://example.invalid/origin.git", ()),
        ("https://example.invalid/new-origin.git", ()),
    ]
    assert _cached_keys(repo) == (
        ("origin", "https://example.invalid/new-origin.git", ()),
    )


def test_config_change_prunes_stale_client_without_network_query(tmp_path, monkeypatch):
    native = "3" * 40
    repo = _repo(tmp_path, native)
    created = []

    class FakeObjectInfoClient:
        def __init__(self, url, *, server_options=()):
            created.append((url, tuple(server_options)))

        def query_sizes(self, oids):
            return {oid: 11 for oid in oids}

    _reset_client_cache(monkeypatch)
    monkeypatch.setattr(refresh, "SmartHttpV2ObjectInfoClient", FakeObjectInfoClient)

    assert refresh.refresh_promisor_sizes(repo, (native,)) == {native: 11}
    repo.add_remote("origin", "https://example.invalid/reconfigured.git")
    assert refresh.refresh_promisor_sizes(repo, (native,)) == {native: 11}

    assert created == [("https://example.invalid/origin.git", ())]
    assert _cached_keys(repo) == ()


def test_changed_server_options_replace_only_stale_effective_key(tmp_path, monkeypatch):
    native_a = "4" * 40
    native_b = "5" * 40
    repo = _repo(tmp_path, native_a, native_b)
    options = [()]
    created = []

    class FakeObjectInfoClient:
        def __init__(self, url, *, server_options=()):
            created.append((url, tuple(server_options)))

        def query_sizes(self, oids):
            return {oid: 13 for oid in oids}

    _reset_client_cache(monkeypatch)
    monkeypatch.setattr(refresh, "SmartHttpV2ObjectInfoClient", FakeObjectInfoClient)
    monkeypatch.setattr(
        refresh,
        "configured_server_options",
        lambda repo, remote: list(options[0]),
    )

    assert refresh.refresh_promisor_sizes(repo, (native_a,)) == {native_a: 13}
    options[0] = ("trace=1",)
    assert refresh.refresh_promisor_sizes(repo, (native_b,)) == {native_b: 13}

    assert created == [
        ("https://example.invalid/origin.git", ()),
        ("https://example.invalid/origin.git", ("trace=1",)),
    ]
    assert _cached_keys(repo) == (
        ("origin", "https://example.invalid/origin.git", ("trace=1",)),
    )


def test_removed_promisor_remote_prunes_only_removed_client(tmp_path, monkeypatch):
    native = "6" * 40
    repo = _repo(tmp_path, native)
    repo.add_remote("zbackup", "https://example.invalid/backup.git")
    update_promisor_state(repo.pygit_dir, remote="zbackup", filter_spec="blob:none")

    class FakeObjectInfoClient:
        def __init__(self, url, *, server_options=()):
            self.url = url

        def query_sizes(self, oids):
            return {oid: 17 for oid in oids}

    _reset_client_cache(monkeypatch)
    monkeypatch.setattr(refresh, "SmartHttpV2ObjectInfoClient", FakeObjectInfoClient)

    origin = refresh._object_info_client(
        repo,
        "origin",
        "https://example.invalid/origin.git",
        (),
    )
    refresh._object_info_client(
        repo,
        "zbackup",
        "https://example.invalid/backup.git",
        (),
    )
    repo.remove_remote("zbackup")

    assert refresh.refresh_promisor_sizes(repo, (native,)) == {native: 17}
    assert _cached_keys(repo) == (
        ("origin", "https://example.invalid/origin.git", ()),
    )
    assert refresh._OBJECT_INFO_CLIENTS[repo][
        ("origin", "https://example.invalid/origin.git", ())
    ] is origin


def test_current_unsupported_capability_client_survives_pruning(tmp_path, monkeypatch):
    native = "7" * 40
    repo = _repo(tmp_path, native)
    created = []

    class FakeObjectInfoClient:
        def __init__(self, *args, **kwargs):
            created.append(self)

        def query_sizes(self, oids):
            raise refresh.ObjectInfoUnsupportedError("object-info unavailable")

    _reset_client_cache(monkeypatch)
    monkeypatch.setattr(refresh, "SmartHttpV2ObjectInfoClient", FakeObjectInfoClient)

    assert refresh.refresh_promisor_sizes(repo, (native,)) == {}
    assert refresh.refresh_promisor_sizes(repo, (native,)) == {}

    assert len(created) == 1
    assert len(_cached_keys(repo)) == 1


def test_strict_framing_failure_evicts_client_and_falls_back(tmp_path, monkeypatch):
    native = "8" * 40
    repo = _repo(tmp_path, native)
    repo.add_remote("zbackup", "https://example.invalid/backup.git")
    update_promisor_state(repo.pygit_dir, remote="zbackup", filter_spec="blob:none")
    created = []

    class FakeObjectInfoClient:
        def __init__(self, url, *, server_options=()):
            self.url = url
            created.append(url)

        def query_sizes(self, oids):
            if self.url.endswith("origin.git"):
                payload = pkt_line(b"size\n")
                payload += pkt_line(f"{oids[0]} 19\n".encode())
                parse_object_info_size_response(payload)
                raise AssertionError("truncated object-info response was accepted")
            return {oid: 23 for oid in oids}

    _reset_client_cache(monkeypatch)
    monkeypatch.setattr(refresh, "SmartHttpV2ObjectInfoClient", FakeObjectInfoClient)

    assert refresh.refresh_promisor_sizes(repo, (native,)) == {native: 23}
    assert created == [
        "https://example.invalid/origin.git",
        "https://example.invalid/backup.git",
    ]
    assert _cached_keys(repo) == (
        ("zbackup", "https://example.invalid/backup.git", ()),
    )
