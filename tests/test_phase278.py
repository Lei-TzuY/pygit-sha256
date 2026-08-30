from __future__ import annotations

from typing import Optional

import pytest

from pygit import rev_list_filter_blob_limit_cli as blob_limit
from pygit import rev_list_in_commit_order_blob_limit_cli as ordered_blob_limit
from pygit.promisor import update_promisor_state
from pygit.promisor_object_inventory import PromisorObjectInventoryEntry
from pygit.repo import Repository


def _promised_blob(repo, native_oid: str, *, size: Optional[int]):
    update_promisor_state(repo.pygit_dir, promised={native_oid: "blob"})
    if size is not None:
        update_promisor_state(repo.pygit_dir, sizes={native_oid: size})
    return PromisorObjectInventoryEntry(
        type_name="blob",
        native_oid=native_oid,
        path="payload.bin",
    )


def test_promised_blob_exact_threshold_is_filtered_without_materialization(
    tmp_path, monkeypatch
):
    repo = Repository.init(str(tmp_path / "repo"))
    native = "a" * 40
    entry = _promised_blob(repo, native, size=8)

    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_object",
        lambda *args, **kwargs: pytest.fail("blob-limit classification must not single-fetch"),
    )
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_objects",
        lambda *args, **kwargs: pytest.fail("blob-limit classification must not batch-fetch"),
    )

    blob_limit._ensure_missing_blobs_are_classifiable(repo, (entry,))
    assert not blob_limit._entry_is_kept(repo, entry, limit=8)
    assert blob_limit._entry_is_kept(repo, entry, limit=9)


def test_promised_blob_missing_record_obeys_trusted_size(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    native = "b" * 40
    _promised_blob(repo, native, size=12)

    line = f"?{native} path=payload.bin type=blob"
    assert not blob_limit._keep_line(repo, line, limit=12)
    assert blob_limit._keep_line(repo, line, limit=13)


def test_missing_promisor_size_remains_strict_error(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    native = "c" * 40
    entry = _promised_blob(repo, native, size=None)

    with pytest.raises(RuntimeError, match="promisor size metadata is unavailable"):
        blob_limit._ensure_missing_blobs_are_classifiable(repo, (entry,))
    with pytest.raises(RuntimeError, match="promisor size metadata is unavailable"):
        blob_limit._entry_is_kept(repo, entry, limit=100)


def test_missing_non_blob_is_not_removed_by_blob_limit(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    native = "d" * 40
    update_promisor_state(repo.pygit_dir, promised={native: "tree"})
    entry = PromisorObjectInventoryEntry(
        type_name="tree",
        native_oid=native,
        path="subdir",
    )

    blob_limit._ensure_missing_blobs_are_classifiable(repo, (entry,))
    assert blob_limit._entry_is_kept(repo, entry, limit=0)
    assert blob_limit._keep_line(repo, f"?{native} type=tree", limit=0)


def test_ordered_blob_limit_keeps_small_promised_blob_in_place(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    native = "e" * 40
    commit = PromisorObjectInventoryEntry(type_name="commit", oid="1" * 64)
    promised = _promised_blob(repo, native, size=3)
    tree = PromisorObjectInventoryEntry(type_name="tree", oid="2" * 64, path="")

    filtered = ordered_blob_limit._apply_blob_limit(
        repo,
        (commit, promised, tree),
        limit=4,
    )
    assert filtered == (commit, promised, tree)

    filtered = ordered_blob_limit._apply_blob_limit(
        repo,
        (commit, promised, tree),
        limit=3,
    )
    assert filtered == (commit, tree)


def test_promisor_size_metadata_does_not_create_local_sha256_identity(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    native = "f" * 40
    entry = _promised_blob(repo, native, size=5)

    assert entry.oid is None
    assert entry.native_oid == native
    assert len(entry.native_oid) == 40
    assert blob_limit._entry_is_kept(repo, entry, limit=6)
    assert entry.oid is None
