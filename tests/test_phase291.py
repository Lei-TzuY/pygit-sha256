from __future__ import annotations

from weakref import WeakKeyDictionary

import pytest

from pygit import promisor_size_refresh as refresh
from pygit.promisor import update_promisor_state
from pygit.protocol_v2 import ProtocolV2Capabilities, SmartHttpV2QueryClient
from pygit.protocol_v2_object_info import (
    ObjectInfoUnsupportedError,
    SmartHttpV2ObjectInfoClient,
    build_object_info_size_request,
)
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


def test_missing_object_info_uses_specific_runtime_error():
    native = "1" * 40
    capabilities = ProtocolV2Capabilities({"fetch": None})

    with pytest.raises(ObjectInfoUnsupportedError, match="does not advertise object-info"):
        build_object_info_size_request(capabilities, (native,))

    assert issubclass(ObjectInfoUnsupportedError, RuntimeError)


def test_object_info_client_caches_negative_capability_discovery(monkeypatch):
    native = "2" * 40
    discoveries = []

    def fake_discover(self):
        discoveries.append(self.url)
        return ProtocolV2Capabilities({"fetch": None})

    monkeypatch.setattr(SmartHttpV2QueryClient, "discover_capabilities", fake_discover)

    client = SmartHttpV2ObjectInfoClient("https://example.invalid/repo.git")
    for _ in range(2):
        with pytest.raises(ObjectInfoUnsupportedError):
            client.query_sizes((native,))

    assert discoveries == ["https://example.invalid/repo.git"]


def test_refresh_retains_explicitly_unsupported_client_across_calls(tmp_path, monkeypatch):
    native = "3" * 40
    repo = _repo(tmp_path, native)
    created = []
    queries = []

    class FakeObjectInfoClient:
        def __init__(self, url, *, server_options=()):
            self.generation = len(created)
            created.append((url, tuple(server_options)))

        def query_sizes(self, oids):
            queries.append((self.generation, tuple(oids)))
            raise ObjectInfoUnsupportedError("object-info unavailable")

    monkeypatch.setattr(refresh, "_OBJECT_INFO_CLIENTS", WeakKeyDictionary())
    monkeypatch.setattr(refresh, "SmartHttpV2ObjectInfoClient", FakeObjectInfoClient)

    assert refresh.refresh_promisor_sizes(repo, (native,)) == {}
    assert refresh.refresh_promisor_sizes(repo, (native,)) == {}

    assert created == [("https://example.invalid/origin.git", ())]
    assert queries == [(0, (native,)), (0, (native,))]


def test_unsupported_remote_still_falls_back_and_both_clients_are_reused(
    tmp_path, monkeypatch
):
    native_a = "4" * 40
    native_b = "5" * 40
    repo = _repo(tmp_path, native_a, native_b)
    repo.add_remote("zbackup", "https://example.invalid/backup.git")
    update_promisor_state(
        repo.pygit_dir,
        remote="zbackup",
        filter_spec="blob:none",
    )
    created = []
    calls = []

    class FakeObjectInfoClient:
        def __init__(self, url, *, server_options=()):
            self.url = url
            created.append((url, tuple(server_options)))

        def query_sizes(self, oids):
            calls.append((self.url, tuple(oids)))
            if self.url.endswith("origin.git"):
                raise ObjectInfoUnsupportedError("object-info unavailable")
            return {oid: 20 + index for index, oid in enumerate(oids)}

    monkeypatch.setattr(refresh, "_OBJECT_INFO_CLIENTS", WeakKeyDictionary())
    monkeypatch.setattr(refresh, "SmartHttpV2ObjectInfoClient", FakeObjectInfoClient)

    assert refresh.refresh_promisor_sizes(repo, (native_a,)) == {native_a: 20}
    assert refresh.refresh_promisor_sizes(repo, (native_b,)) == {native_b: 20}

    assert created == [
        ("https://example.invalid/origin.git", ()),
        ("https://example.invalid/backup.git", ()),
    ]
    assert calls == [
        ("https://example.invalid/origin.git", (native_a,)),
        ("https://example.invalid/backup.git", (native_a,)),
        ("https://example.invalid/origin.git", (native_b,)),
        ("https://example.invalid/backup.git", (native_b,)),
    ]


def test_generic_runtime_failure_still_evicts_client(tmp_path, monkeypatch):
    native = "6" * 40
    repo = _repo(tmp_path, native)
    created = []

    class FakeObjectInfoClient:
        def __init__(self, *args, **kwargs):
            self.generation = len(created)
            created.append(self.generation)

        def query_sizes(self, oids):
            raise RuntimeError(f"broken session {self.generation}")

    monkeypatch.setattr(refresh, "_OBJECT_INFO_CLIENTS", WeakKeyDictionary())
    monkeypatch.setattr(refresh, "SmartHttpV2ObjectInfoClient", FakeObjectInfoClient)

    assert refresh.refresh_promisor_sizes(repo, (native,)) == {}
    assert refresh.refresh_promisor_sizes(repo, (native,)) == {}
    assert created == [0, 1]
