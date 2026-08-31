from __future__ import annotations

from pathlib import Path

import pytest

import pygit.protocol_v2_packfile_uri_incremental_fetch as phase336
from pygit.loose_object_map import lookup_local_sha256, lookup_native_sha1
from pygit.objects.commit import CommitObject
from pygit.objects.tree import TreeObject
from pygit.protocol_v2_packfile_uri_batch import DownloadedPackfileUriBatch
from pygit.protocol_v2_packfile_uri_incremental import PackfileUriIncrementalState
from pygit.protocol_v2_packfile_uri_incremental_fetch import (
    IncrementalPackfileUriFetchTransactionResult,
    execute_incremental_packfile_uri_fetch_transaction,
)
from pygit.protocol_v2_packfile_uri_refs import PackfileUriRefPublication
from pygit.protocol_v2_packfile_uri_stage import StagedPackfileUriImport
from pygit.refs import ZERO_SHA
from pygit.remote import NativeExporter
from pygit.repo import Repository


EMPTY_BATCH = DownloadedPackfileUriBatch((), {}, 0)


def _native_commit(repo: Repository):
    tree = TreeObject()
    tree_oid = repo.store.write(tree)
    commit = CommitObject(tree=tree_oid, message="phase336\n")
    local_oid = repo.store.write(commit)
    exporter = NativeExporter(repo.store)
    native_oid = exporter.export_oid(local_oid)
    native_objects = dict(exporter.objects)
    native_to_local = {
        native: local for local, native in exporter.converted.items()
    }
    return local_oid, native_oid, native_objects, native_to_local


def test_incremental_transaction_publishes_lmap_before_ref_commit(tmp_path: Path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    native = "1" * 40
    staged = StagedPackfileUriImport({native: "2" * 64}, ("2" * 64,))
    incremental = PackfileUriIncrementalState((), {}, ())
    publication = PackfileUriRefPublication(native, ZERO_SHA)
    events: list[str] = []
    object_map = object()
    certificate = object()

    monkeypatch.setattr(phase336, "download_packfile_uris", lambda *a, **k: EMPTY_BATCH)
    monkeypatch.setattr(phase336, "stage_packfile_uri_import", lambda *a, **k: staged)

    def publish_map(repo_arg, staged_arg):
        assert repo_arg is repo
        assert staged_arg is staged
        events.append("map")
        return object_map

    def certify(*args, **kwargs):
        events.append("certify")
        return certificate

    def publish_refs(*args, **kwargs):
        events.append("refs")
        return {"refs/remotes/origin/main": "2" * 64}

    monkeypatch.setattr(phase336, "publish_staged_loose_object_map", publish_map)
    monkeypatch.setattr(phase336, "certify_packfile_uri_roots", certify)
    monkeypatch.setattr(phase336, "_acquire_publication_guard_locks", lambda repo: [])
    monkeypatch.setattr(phase336, "_assert_publication_state_unchanged", lambda *a, **k: None)
    monkeypatch.setattr(phase336, "_release_publication_guard_locks", lambda locks: None)
    monkeypatch.setattr(phase336, "publish_packfile_uri_refs", publish_refs)

    result = execute_incremental_packfile_uri_fetch_transaction(
        repo,
        (),
        {native: object()},
        {native: b"commit"},
        {"refs/remotes/origin/main": publication},
        incremental,
    )

    assert isinstance(result, IncrementalPackfileUriFetchTransactionResult)
    assert result.object_map is object_map
    assert result.staged is staged
    assert result.certificate is certificate
    assert events == ["map", "certify", "refs"]


def test_lmap_publication_failure_aborts_before_certification_or_refs(tmp_path: Path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    native = "3" * 40
    staged = StagedPackfileUriImport({native: "4" * 64}, ("4" * 64,))
    incremental = PackfileUriIncrementalState((), {}, ())
    publication = PackfileUriRefPublication(native, ZERO_SHA)

    monkeypatch.setattr(phase336, "download_packfile_uris", lambda *a, **k: EMPTY_BATCH)
    monkeypatch.setattr(phase336, "stage_packfile_uri_import", lambda *a, **k: staged)
    monkeypatch.setattr(
        phase336,
        "publish_staged_loose_object_map",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("map failed")),
    )
    monkeypatch.setattr(
        phase336,
        "certify_packfile_uri_roots",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not certify")),
    )
    monkeypatch.setattr(
        phase336,
        "publish_packfile_uri_refs",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not publish refs")),
    )

    with pytest.raises(RuntimeError, match="map failed"):
        execute_incremental_packfile_uri_fetch_transaction(
            repo,
            (),
            {native: object()},
            {native: b"commit"},
            {"refs/remotes/origin/main": publication},
            incremental,
        )


def test_real_incremental_transaction_makes_new_tip_lookupable_for_next_fetch(tmp_path: Path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    local_tip, native_tip, native_objects, native_to_local = _native_commit(repo)

    # Remove the exported local graph so the transaction has to republish the
    # native objects through the SHA-256 staging boundary rather than inheriting
    # the exporter-created local copies as proof of compatibility identity.
    for local_oid in sorted(set(native_to_local.values())):
        repo.store.delete(local_oid)

    publication = PackfileUriRefPublication(native_tip, ZERO_SHA)
    incremental = PackfileUriIncrementalState((), {}, ())
    monkeypatch.setattr(phase336, "download_packfile_uris", lambda *a, **k: EMPTY_BATCH)

    result = execute_incremental_packfile_uri_fetch_transaction(
        repo,
        (),
        native_objects,
        {native_tip: b"commit"},
        {"refs/remotes/origin/main": publication},
        incremental,
    )

    assert result.published_refs["refs/remotes/origin/main"] == local_tip
    assert lookup_local_sha256(repo, native_tip) == local_tip
    assert lookup_native_sha1(repo, local_tip) == native_tip
    assert result.object_map.object_count == len(native_to_local)


def test_ref_publication_failure_can_leave_only_valid_immutable_lmap(tmp_path: Path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    local_tip, native_tip, native_objects, native_to_local = _native_commit(repo)
    for local_oid in sorted(set(native_to_local.values())):
        repo.store.delete(local_oid)

    publication = PackfileUriRefPublication(native_tip, ZERO_SHA)
    incremental = PackfileUriIncrementalState((), {}, ())
    monkeypatch.setattr(phase336, "download_packfile_uris", lambda *a, **k: EMPTY_BATCH)
    monkeypatch.setattr(
        phase336,
        "publish_packfile_uri_refs",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("stale CAS")),
    )

    with pytest.raises(RuntimeError, match="stale CAS"):
        execute_incremental_packfile_uri_fetch_transaction(
            repo,
            (),
            native_objects,
            {native_tip: b"commit"},
            {"refs/remotes/origin/main": publication},
            incremental,
        )

    assert repo.refs.get("refs/remotes/origin/main") is None
    assert lookup_local_sha256(repo, native_tip) == local_tip
    assert lookup_native_sha1(repo, local_tip) == native_tip
