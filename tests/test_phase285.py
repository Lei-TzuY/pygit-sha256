from __future__ import annotations

import pytest

from pygit import rev_list_filter_blob_limit_cli as blob_limit
from pygit.promisor import promised_size, update_promisor_state
from pygit.promisor_object_inventory import PromisorObjectInventoryEntry
from pygit.repo import Repository


def _entry(native_oid: str, path: str):
    return PromisorObjectInventoryEntry(
        type_name="blob",
        native_oid=native_oid,
        path=path,
    )


def _repo_with_promises(tmp_path, *native_oids: str):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.invalid/repo.git")
    update_promisor_state(
        repo.pygit_dir,
        remote="origin",
        filter_spec="blob:none",
        promised={oid: "blob" for oid in native_oids},
    )
    return repo


def test_preflight_batches_all_missing_blob_sizes_into_one_query(tmp_path, monkeypatch):
    native_a = "1" * 40
    native_b = "2" * 40
    native_c = "3" * 40
    repo = _repo_with_promises(tmp_path, native_a, native_b, native_c)
    update_promisor_state(repo.pygit_dir, sizes={native_b: 7})

    queries = []

    class FakeObjectInfoClient:
        def __init__(self, url, *, server_options=()):
            assert url == "https://example.invalid/repo.git"

        def query_sizes(self, oids):
            queries.append(tuple(oids))
            return {native_a: 4, native_c: 12}

    monkeypatch.setattr(
        "pygit.promisor_size_refresh.SmartHttpV2ObjectInfoClient",
        FakeObjectInfoClient,
    )

    entries = (
        _entry(native_a, "a.bin"),
        _entry(native_b, "b.bin"),
        _entry(native_c, "c.bin"),
    )
    blob_limit._ensure_missing_blobs_are_classifiable(repo, entries)

    assert queries == [(native_a, native_c)]
    assert promised_size(repo.pygit_dir, native_a) == 4
    assert promised_size(repo.pygit_dir, native_b) == 7
    assert promised_size(repo.pygit_dir, native_c) == 12


def test_preflight_deduplicates_repeated_inventory_identities(tmp_path, monkeypatch):
    native = "a" * 40
    repo = _repo_with_promises(tmp_path, native)
    queries = []

    class FakeObjectInfoClient:
        def __init__(self, *args, **kwargs):
            pass

        def query_sizes(self, oids):
            queries.append(tuple(oids))
            return {native: 5}

    monkeypatch.setattr(
        "pygit.promisor_size_refresh.SmartHttpV2ObjectInfoClient",
        FakeObjectInfoClient,
    )

    blob_limit._ensure_missing_blobs_are_classifiable(
        repo,
        (_entry(native, "one.bin"), _entry(native.upper(), "two.bin")),
    )

    assert queries == [(native,)]


def test_partial_batch_result_stays_strict_without_per_object_retry(tmp_path, monkeypatch):
    native_a = "b" * 40
    native_b = "c" * 40
    repo = _repo_with_promises(tmp_path, native_a, native_b)
    queries = []

    class FakeObjectInfoClient:
        def __init__(self, *args, **kwargs):
            pass

        def query_sizes(self, oids):
            queries.append(tuple(oids))
            return {native_a: 9, native_b: None}

    monkeypatch.setattr(
        "pygit.promisor_size_refresh.SmartHttpV2ObjectInfoClient",
        FakeObjectInfoClient,
    )

    with pytest.raises(RuntimeError, match=native_b):
        blob_limit._ensure_missing_blobs_are_classifiable(
            repo,
            (_entry(native_a, "a.bin"), _entry(native_b, "b.bin")),
        )

    assert queries == [(native_a, native_b)]
    assert promised_size(repo.pygit_dir, native_a) == 9
    assert promised_size(repo.pygit_dir, native_b) is None


def test_preflight_with_all_sizes_persisted_does_not_construct_client(tmp_path, monkeypatch):
    native_a = "d" * 40
    native_b = "e" * 40
    repo = _repo_with_promises(tmp_path, native_a, native_b)
    update_promisor_state(repo.pygit_dir, sizes={native_a: 1, native_b: 2})

    class UnexpectedObjectInfoClient:
        def __init__(self, *args, **kwargs):
            pytest.fail("persisted batch must not query metadata again")

    monkeypatch.setattr(
        "pygit.promisor_size_refresh.SmartHttpV2ObjectInfoClient",
        UnexpectedObjectInfoClient,
    )

    blob_limit._ensure_missing_blobs_are_classifiable(
        repo,
        (_entry(native_a, "a.bin"), _entry(native_b, "b.bin")),
    )


def test_batch_preflight_preserves_native_sha1_only(tmp_path, monkeypatch):
    native_a = "f" * 40
    native_b = "0" * 40
    repo = _repo_with_promises(tmp_path, native_a, native_b)

    class FakeObjectInfoClient:
        def __init__(self, *args, **kwargs):
            pass

        def query_sizes(self, oids):
            assert tuple(oids) == tuple(sorted((native_a, native_b)))
            assert all(len(oid) == 40 for oid in oids)
            return {native_a: 3, native_b: 4}

    monkeypatch.setattr(
        "pygit.promisor_size_refresh.SmartHttpV2ObjectInfoClient",
        FakeObjectInfoClient,
    )

    entries = (_entry(native_a, "a.bin"), _entry(native_b, "b.bin"))
    blob_limit._ensure_missing_blobs_are_classifiable(repo, entries)

    assert all(entry.oid is None for entry in entries)
    assert all(len(entry.native_oid) == 40 for entry in entries)
