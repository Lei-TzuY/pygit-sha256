from __future__ import annotations

from email.message import Message
from weakref import WeakKeyDictionary

from pygit import promisor_size_refresh as refresh
from pygit.promisor import update_promisor_state
from pygit.protocol_v2 import ProtocolV2Capabilities
from pygit.protocol_v2_object_info import SmartHttpV2ObjectInfoClient
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


def _object_info_body(oid: str, size: int) -> bytes:
    return pkt_line(b"size\n") + pkt_line(f"{oid} {size}\n".encode()) + b"0000"


class _Response:
    def __init__(self, body: bytes, content_type: str):
        self.body = body
        self.read_calls = 0
        self.headers = Message()
        self.headers["Content-Type"] = content_type

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        self.read_calls += 1
        return self.body


class _IntegratedObjectInfoClient(SmartHttpV2ObjectInfoClient):
    """Use the real Phase294/296 POST path while avoiding discovery I/O."""

    def _discover_object_info_capabilities(self):
        return ProtocolV2Capabilities({"object-info": None})


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


def test_wrong_http_media_type_fails_before_read_evicts_and_falls_back(
    tmp_path, monkeypatch
):
    native = "8" * 40
    repo = _repo(tmp_path, native)
    repo.add_remote("zbackup", "https://example.invalid/backup.git")
    update_promisor_state(repo.pygit_dir, remote="zbackup", filter_spec="blob:none")

    origin_response = _Response(_object_info_body(native, 19), "text/html")
    backup_response = _Response(
        _object_info_body(native, 23),
        "application/x-git-upload-pack-result",
    )

    def fake_urlopen(request, timeout):
        if "origin.git/" in request.full_url:
            return origin_response
        if "backup.git/" in request.full_url:
            return backup_response
        raise AssertionError(request.full_url)

    _reset_client_cache(monkeypatch)
    monkeypatch.setattr(refresh, "SmartHttpV2ObjectInfoClient", _IntegratedObjectInfoClient)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    assert refresh.refresh_promisor_sizes(repo, (native,)) == {native: 23}
    assert origin_response.read_calls == 0
    assert backup_response.read_calls == 1
    assert _cached_keys(repo) == (
        ("zbackup", "https://example.invalid/backup.git", ()),
    )


def test_valid_http_media_type_truncated_body_evicts_and_falls_back(
    tmp_path, monkeypatch
):
    native = "9" * 40
    repo = _repo(tmp_path, native)
    repo.add_remote("zbackup", "https://example.invalid/backup.git")
    update_promisor_state(repo.pygit_dir, remote="zbackup", filter_spec="blob:none")

    truncated = pkt_line(b"size\n") + pkt_line(f"{native} 29\n".encode())
    origin_response = _Response(truncated, "application/x-git-upload-pack-result")
    backup_response = _Response(
        _object_info_body(native, 31),
        "application/x-git-upload-pack-result",
    )

    def fake_urlopen(request, timeout):
        if "origin.git/" in request.full_url:
            return origin_response
        if "backup.git/" in request.full_url:
            return backup_response
        raise AssertionError(request.full_url)

    _reset_client_cache(monkeypatch)
    monkeypatch.setattr(refresh, "SmartHttpV2ObjectInfoClient", _IntegratedObjectInfoClient)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    assert refresh.refresh_promisor_sizes(repo, (native,)) == {native: 31}
    assert origin_response.read_calls == 1
    assert backup_response.read_calls == 1
    assert _cached_keys(repo) == (
        ("zbackup", "https://example.invalid/backup.git", ()),
    )
