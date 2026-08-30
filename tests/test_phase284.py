from __future__ import annotations

import pytest

from pygit import rev_list_filter_blob_limit_cli as blob_limit
from pygit.promisor import promised_size, read_promisor_state, update_promisor_state
from pygit.promisor_object_inventory import PromisorObjectInventoryEntry
from pygit.repo import Repository


def _promisor_repo(tmp_path, native_oid: str):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.invalid/repo.git")
    update_promisor_state(
        repo.pygit_dir,
        remote="origin",
        filter_spec="blob:none",
        promised={native_oid: "blob"},
    )
    entry = PromisorObjectInventoryEntry(
        type_name="blob",
        native_oid=native_oid,
        path="payload.bin",
    )
    return repo, entry


def _forbid_content_fetch(monkeypatch):
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_object",
        lambda *args, **kwargs: pytest.fail("size refresh must not single-fetch content"),
    )
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_objects",
        lambda *args, **kwargs: pytest.fail("size refresh must not batch-fetch content"),
    )


def test_blob_limit_lazily_refreshes_missing_promisor_size(tmp_path, monkeypatch):
    native = "a" * 40
    repo, entry = _promisor_repo(tmp_path, native)
    _forbid_content_fetch(monkeypatch)

    calls = []

    class FakeObjectInfoClient:
        def __init__(self, url, *, server_options=()):
            calls.append(("init", url, tuple(server_options)))

        def query_sizes(self, oids):
            calls.append(("query", tuple(oids)))
            return {native: 8}

    monkeypatch.setattr(
        "pygit.promisor_size_refresh.SmartHttpV2ObjectInfoClient",
        FakeObjectInfoClient,
    )
    monkeypatch.setattr(
        "pygit.promisor_size_refresh.configured_server_options",
        lambda _repo, remote: ["trace=phase284"] if remote == "origin" else [],
    )

    assert promised_size(repo.pygit_dir, native) is None
    blob_limit._ensure_missing_blobs_are_classifiable(repo, (entry,))

    assert promised_size(repo.pygit_dir, native) == 8
    assert calls == [
        ("init", "https://example.invalid/repo.git", ("trace=phase284",)),
        ("query", (native,)),
    ]
    assert not blob_limit._entry_is_kept(repo, entry, limit=8)
    assert blob_limit._entry_is_kept(repo, entry, limit=9)


def test_lazy_refresh_reuses_persisted_size_without_remote_query(tmp_path, monkeypatch):
    native = "b" * 40
    repo, entry = _promisor_repo(tmp_path, native)
    update_promisor_state(repo.pygit_dir, sizes={native: 3})

    class UnexpectedObjectInfoClient:
        def __init__(self, *args, **kwargs):
            pytest.fail("persisted size must avoid a second metadata query")

    monkeypatch.setattr(
        "pygit.promisor_size_refresh.SmartHttpV2ObjectInfoClient",
        UnexpectedObjectInfoClient,
    )

    blob_limit._ensure_missing_blobs_are_classifiable(repo, (entry,))
    assert blob_limit._entry_is_kept(repo, entry, limit=4)


def test_unsupported_object_info_keeps_strict_missing_size_error(tmp_path, monkeypatch):
    native = "c" * 40
    repo, entry = _promisor_repo(tmp_path, native)
    _forbid_content_fetch(monkeypatch)

    class UnsupportedObjectInfoClient:
        def __init__(self, *args, **kwargs):
            pass

        def query_sizes(self, oids):
            assert tuple(oids) == (native,)
            raise RuntimeError("Remote protocol-v2 server does not advertise object-info")

    monkeypatch.setattr(
        "pygit.promisor_size_refresh.SmartHttpV2ObjectInfoClient",
        UnsupportedObjectInfoClient,
    )

    with pytest.raises(RuntimeError, match="promisor size metadata is unavailable"):
        blob_limit._ensure_missing_blobs_are_classifiable(repo, (entry,))
    assert promised_size(repo.pygit_dir, native) is None


def test_unknown_object_info_result_keeps_strict_error(tmp_path, monkeypatch):
    native = "d" * 40
    repo, entry = _promisor_repo(tmp_path, native)

    class UnknownObjectInfoClient:
        def __init__(self, *args, **kwargs):
            pass

        def query_sizes(self, oids):
            return {native: None}

    monkeypatch.setattr(
        "pygit.promisor_size_refresh.SmartHttpV2ObjectInfoClient",
        UnknownObjectInfoClient,
    )

    with pytest.raises(RuntimeError, match="promisor size metadata is unavailable"):
        blob_limit._entry_is_kept(repo, entry, limit=100)


def test_refresh_never_creates_surrogate_local_sha256_identity(tmp_path, monkeypatch):
    native = "e" * 40
    repo, entry = _promisor_repo(tmp_path, native)

    class FakeObjectInfoClient:
        def __init__(self, *args, **kwargs):
            pass

        def query_sizes(self, oids):
            return {native: 5}

    monkeypatch.setattr(
        "pygit.promisor_size_refresh.SmartHttpV2ObjectInfoClient",
        FakeObjectInfoClient,
    )

    assert blob_limit._entry_is_kept(repo, entry, limit=6)
    state = read_promisor_state(repo.pygit_dir)
    assert state["sizes"] == {native: 5}
    assert state["resolved"] == {}
    assert state["promised"] == {native: "blob"}
    assert entry.oid is None
    assert entry.native_oid == native
    assert len(entry.native_oid) == 40


def test_refresh_tries_promisor_remotes_in_deterministic_order(tmp_path, monkeypatch):
    native = "f" * 40
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("zeta", "https://zeta.invalid/repo.git")
    repo.add_remote("alpha", "https://alpha.invalid/repo.git")
    update_promisor_state(
        repo.pygit_dir,
        remote="zeta",
        filter_spec="blob:none",
        promised={native: "blob"},
    )
    update_promisor_state(repo.pygit_dir, remote="alpha", filter_spec="blob:none")

    queried = []

    class FakeObjectInfoClient:
        def __init__(self, url, *, server_options=()):
            self.url = url

        def query_sizes(self, oids):
            queried.append(self.url)
            if "alpha" in self.url:
                return {native: None}
            return {native: 7}

    monkeypatch.setattr(
        "pygit.promisor_size_refresh.SmartHttpV2ObjectInfoClient",
        FakeObjectInfoClient,
    )

    assert blob_limit._promised_blob_size(repo, native) == 7
    assert queried == [
        "https://alpha.invalid/repo.git",
        "https://zeta.invalid/repo.git",
    ]
