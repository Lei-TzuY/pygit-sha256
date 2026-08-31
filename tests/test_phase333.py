from pathlib import Path

import pytest

from pygit.loose_object_map import publish_staged_loose_object_map
from pygit.objects.blob import BlobObject
from pygit.objects.commit import CommitObject
from pygit.objects.tree import TreeObject
from pygit.protocol_v2_packfile_uri_incremental import (
    plan_packfile_uri_incremental_state,
)
from pygit.protocol_v2_packfile_uri_refs import PackfileUriRefPublication
from pygit.protocol_v2_packfile_uri_stage import StagedPackfileUriImport
from pygit.protocol_v2_packfile_uri_tracking import PackfileUriRemoteTrackingPlan
from pygit.refs import ZERO_SHA
from pygit.remote import NativeExporter
from pygit.repo import Repository


def _local_commit_graph(repo: Repository, *, shallow: bool = False):
    blob = BlobObject(b"phase333 mapped payload\n")
    blob_oid = repo.store.write(blob)

    tree = TreeObject()
    tree.add_entry("100644", "payload.txt", blob_oid)
    tree_oid = repo.store.write(tree)

    commit = CommitObject(
        tree=tree_oid,
        message="phase333\n",
        native_parents=["ab" * 20] if shallow else None,
    )
    commit_oid = repo.store.write(commit)
    return commit_oid, tree_oid, blob_oid


def _native_mapping(repo: Repository, local_tip: str):
    exporter = NativeExporter(repo.store)
    native_tip = exporter.export_oid(local_tip)
    native_to_local = {
        native: local for local, native in exporter.converted.items()
    }
    return native_tip, native_to_local


def _publish_mapping(repo: Repository, mapping):
    publish_staged_loose_object_map(
        repo,
        StagedPackfileUriImport(
            dict(mapping),
            tuple(sorted(set(mapping.values()))),
        ),
    )


def _plan(old_local_oid: str, *, refs=("refs/remotes/origin/main",)):
    publications = {
        refname: PackfileUriRefPublication("ef" * 20, old_local_oid)
        for refname in refs
    }
    return PackfileUriRemoteTrackingPlan(
        expected_roots={"ef" * 20: b"commit"},
        publications=publications,
        default_branch="main",
    )


def test_complete_lmap_backed_commit_closure_becomes_have_and_known_map(tmp_path: Path):
    repo = Repository.init(str(tmp_path / "repo"))
    commit_oid, _, _ = _local_commit_graph(repo)
    native_tip, native_to_local = _native_mapping(repo, commit_oid)
    _publish_mapping(repo, native_to_local)

    state = plan_packfile_uri_incremental_state(repo, _plan(commit_oid))

    assert state.haves == (native_tip,)
    assert state.known_native_to_local == dict(sorted(native_to_local.items()))
    assert state.fallback_refs == ()


def test_new_tracking_ref_falls_back_without_synthesizing_have(tmp_path: Path):
    repo = Repository.init(str(tmp_path / "repo"))

    state = plan_packfile_uri_incremental_state(repo, _plan(ZERO_SHA))

    assert state.haves == ()
    assert state.known_native_to_local == {}
    assert state.fallback_refs == ("refs/remotes/origin/main",)


def test_incomplete_object_map_falls_back_instead_of_claiming_partial_closure(tmp_path: Path):
    repo = Repository.init(str(tmp_path / "repo"))
    commit_oid, _, _ = _local_commit_graph(repo)
    native_tip, _ = _native_mapping(repo, commit_oid)
    _publish_mapping(repo, {native_tip: commit_oid})

    state = plan_packfile_uri_incremental_state(repo, _plan(commit_oid))

    assert state.haves == ()
    assert state.known_native_to_local == {}
    assert state.fallback_refs == ("refs/remotes/origin/main",)


def test_mapped_but_missing_local_tip_fails_closed(tmp_path: Path):
    repo = Repository.init(str(tmp_path / "repo"))
    commit_oid, _, _ = _local_commit_graph(repo)
    native_tip, native_to_local = _native_mapping(repo, commit_oid)
    _publish_mapping(repo, native_to_local)
    assert native_tip in native_to_local
    assert repo.store.delete(commit_oid)

    with pytest.raises(RuntimeError, match="mapped local object .* is missing"):
        plan_packfile_uri_incremental_state(repo, _plan(commit_oid))


def test_non_commit_tracking_tip_is_never_advertised_as_have(tmp_path: Path):
    repo = Repository.init(str(tmp_path / "repo"))
    blob = BlobObject(b"not a commit\n")
    local = repo.store.write(blob)
    exporter = NativeExporter(repo.store)
    native = exporter.export_oid(local)
    _publish_mapping(repo, {native: local})

    with pytest.raises(ValueError, match="tracking tip must resolve to a local commit"):
        plan_packfile_uri_incremental_state(repo, _plan(local))


def test_shallow_foreign_commit_falls_back_without_implicit_shallow_negotiation(tmp_path: Path):
    repo = Repository.init(str(tmp_path / "repo"))
    commit_oid, _, _ = _local_commit_graph(repo, shallow=True)

    # A shallow local commit cannot be exported through NativeExporter because its
    # unresolved native parent intentionally has no local SHA-256 parent.  Its
    # explicit compatibility identity is enough to exercise the planner's rule.
    _publish_mapping(repo, {"cd" * 20: commit_oid})

    state = plan_packfile_uri_incremental_state(repo, _plan(commit_oid))

    assert state.haves == ()
    assert state.known_native_to_local == {}
    assert state.fallback_refs == ("refs/remotes/origin/main",)


def test_shared_existing_tip_is_deduplicated_but_keeps_complete_known_closure(tmp_path: Path):
    repo = Repository.init(str(tmp_path / "repo"))
    commit_oid, _, _ = _local_commit_graph(repo)
    native_tip, native_to_local = _native_mapping(repo, commit_oid)
    _publish_mapping(repo, native_to_local)

    state = plan_packfile_uri_incremental_state(
        repo,
        _plan(
            commit_oid,
            refs=(
                "refs/remotes/origin/main",
                "refs/remotes/origin/release",
            ),
        ),
    )

    assert state.haves == (native_tip,)
    assert state.known_native_to_local == dict(sorted(native_to_local.items()))
    assert state.fallback_refs == ()
