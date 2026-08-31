from pathlib import Path

from pygit.loose_object_map import publish_staged_loose_object_map
from pygit.objects.blob import BlobObject
from pygit.objects.commit import CommitObject
from pygit.objects.tree import TreeObject
from pygit.protocol_v2_packfile_uri_incremental import plan_packfile_uri_incremental_state
from pygit.protocol_v2_packfile_uri_refs import PackfileUriRefPublication
from pygit.protocol_v2_packfile_uri_stage import StagedPackfileUriImport
from pygit.protocol_v2_packfile_uri_tracking import PackfileUriRemoteTrackingPlan
from pygit.remote import NativeExporter
from pygit.repo import Repository


def _graph(repo: Repository):
    blob = BlobObject(b"phase339 content-authenticated have\n")
    blob_oid = repo.store.write(blob)

    tree = TreeObject()
    tree.add_entry("100644", "payload.txt", blob_oid)
    tree_oid = repo.store.write(tree)

    parent = CommitObject(tree=tree_oid, message="phase339 parent\n")
    parent_oid = repo.store.write(parent)
    tip = CommitObject(tree=tree_oid, parents=[parent_oid], message="phase339 tip\n")
    tip_oid = repo.store.write(tip)
    return tip_oid, parent_oid, tree_oid, blob_oid


def _native_mapping(repo: Repository, local_tip: str):
    exporter = NativeExporter(repo.store)
    native_tip = exporter.export_oid(local_tip)
    mapping = {native: local for local, native in exporter.converted.items()}
    return native_tip, mapping


def _publish(repo: Repository, mapping):
    publish_staged_loose_object_map(
        repo,
        StagedPackfileUriImport(
            dict(mapping),
            tuple(sorted(set(mapping.values()))),
        ),
    )


def _plan(old_local_oid: str):
    return PackfileUriRemoteTrackingPlan(
        expected_roots={"ef" * 20: b"commit"},
        publications={
            "refs/remotes/origin/main": PackfileUriRefPublication(
                "ef" * 20,
                old_local_oid,
            )
        },
        default_branch="main",
    )


def test_genuine_lmap_closure_is_content_authenticated_before_have(tmp_path: Path):
    repo = Repository.init(str(tmp_path / "repo"))
    tip, _, _, _ = _graph(repo)
    native_tip, mapping = _native_mapping(repo, tip)
    _publish(repo, mapping)

    state = plan_packfile_uri_incremental_state(repo, _plan(tip))

    assert state.haves == (native_tip,)
    assert state.known_native_to_local == dict(sorted(mapping.items()))
    assert state.fallback_refs == ()


def test_checksummed_lmap_with_forged_tip_sha1_falls_back(tmp_path: Path):
    repo = Repository.init(str(tmp_path / "repo"))
    tip, _, _, _ = _graph(repo)
    native_tip, mapping = _native_mapping(repo, tip)

    forged_tip = "11" * 20
    assert forged_tip != native_tip
    mapping = dict(mapping)
    del mapping[native_tip]
    mapping[forged_tip] = tip
    _publish(repo, mapping)

    state = plan_packfile_uri_incremental_state(repo, _plan(tip))

    assert state.haves == ()
    assert state.known_native_to_local == {}
    assert state.fallback_refs == ("refs/remotes/origin/main",)


def test_checksummed_lmap_with_forged_dependency_sha1_falls_back(tmp_path: Path):
    repo = Repository.init(str(tmp_path / "repo"))
    tip, _, _, blob = _graph(repo)
    native_tip, mapping = _native_mapping(repo, tip)

    blob_native = next(native for native, local in mapping.items() if local == blob)
    forged_blob = "22" * 20
    assert forged_blob not in mapping
    mapping = dict(mapping)
    del mapping[blob_native]
    mapping[forged_blob] = blob
    _publish(repo, mapping)

    state = plan_packfile_uri_incremental_state(repo, _plan(tip))

    assert state.haves == ()
    assert state.known_native_to_local == {}
    assert state.fallback_refs == ("refs/remotes/origin/main",)


def test_semantic_lmap_authentication_never_rehashes_oid_text(tmp_path: Path):
    repo = Repository.init(str(tmp_path / "repo"))
    tip, _, _, _ = _graph(repo)
    native_tip, mapping = _native_mapping(repo, tip)

    forged_tip = "33" * 20
    mapping = dict(mapping)
    del mapping[native_tip]
    mapping[forged_tip] = tip
    _publish(repo, mapping)

    state = plan_packfile_uri_incremental_state(repo, _plan(tip))

    assert forged_tip not in state.haves
    assert tip not in state.haves
    assert state.haves == ()
