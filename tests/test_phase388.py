import hashlib
import subprocess
from pathlib import Path

import pytest

from pygit.git_bundle import BundlePrerequisite, GitBundlePayload, parse_git_bundle
from pygit.git_bundle_stage import StagedGitBundleImport, stage_git_bundle_import
from pygit.objects import BlobObject, CommitObject
from pygit.remote import NativeObject
from pygit.repo import Repository
from pygit.store import ObjectStore


def _native(type_name: str, data: bytes) -> NativeObject:
    canonical = f"{type_name} {len(data)}\0".encode() + data
    oid = hashlib.sha1(canonical).hexdigest()
    return NativeObject(type_name, data, oid)


def _payload(
    objects,
    *,
    refs=None,
    prerequisites=(),
    filter_spec=None,
    object_format="sha1",
) -> GitBundlePayload:
    refs = refs or {"refs/heads/main": next(iter(objects))}
    return GitBundlePayload(
        version=2,
        object_format=object_format,
        capabilities={},
        prerequisites=tuple(prerequisites),
        refs=dict(refs),
        filter_spec=filter_spec,
        pack=b"",
        pack_version=2,
        pack_entries=len(objects),
        objects=dict(objects),
    )


def _loose_files(store: ObjectStore):
    return sorted(path for path in store.root.rglob("*") if path.is_file())


def _path_snapshot(path: Path):
    return path.exists(), path.read_bytes() if path.exists() else None


def test_stages_simple_native_blob_as_true_local_sha256(tmp_path):
    native = _native("blob", b"hello bundle\n")
    store = ObjectStore(tmp_path / "objects")

    result = stage_git_bundle_import(
        store,
        _payload({native.oid: native}),
    )

    assert isinstance(result, StagedGitBundleImport)
    assert set(result.native_to_local) == {native.oid}
    local_oid = result.native_to_local[native.oid]
    assert len(local_oid) == 64
    int(local_oid, 16)
    assert local_oid != native.oid
    assert result.ref_targets == {"refs/heads/main": local_oid}
    assert result.local_oids == (local_oid,)
    obj = store.read(local_oid)
    assert isinstance(obj, BlobObject)
    assert obj.data == b"hello bundle\n"


def test_imports_entire_graph_not_only_advertised_roots_before_publication(tmp_path):
    advertised = _native("blob", b"advertised")
    missing_oid = "12" * 20
    bad_tree_data = b"100644 missing.txt\x00" + bytes.fromhex(missing_oid)
    bad_tree = _native("tree", bad_tree_data)
    store = ObjectStore(tmp_path / "objects")

    bundle = _payload(
        {advertised.oid: advertised, bad_tree.oid: bad_tree},
        refs={"refs/heads/main": advertised.oid},
    )

    with pytest.raises(KeyError, match="missing object"):
        stage_git_bundle_import(store, bundle)

    assert _loose_files(store) == []


def test_forged_native_content_fails_before_destination_write(tmp_path):
    genuine = _native("blob", b"genuine")
    forged = NativeObject("blob", b"forged", genuine.oid)
    store = ObjectStore(tmp_path / "objects")

    with pytest.raises(ValueError, match="content does not match"):
        stage_git_bundle_import(store, _payload({genuine.oid: forged}))

    assert _loose_files(store) == []


def test_prerequisite_bundle_is_rejected_before_destination_write(tmp_path):
    native = _native("blob", b"payload")
    store = ObjectStore(tmp_path / "objects")
    prerequisite = BundlePrerequisite("34" * 20, b"base")
    bundle = _payload(
        {native.oid: native},
        prerequisites=(prerequisite,),
    )

    with pytest.raises(RuntimeError, match="prerequisites"):
        stage_git_bundle_import(store, bundle)

    assert _loose_files(store) == []


def test_filtered_bundle_is_rejected_before_destination_write(tmp_path):
    native = _native("blob", b"payload")
    store = ObjectStore(tmp_path / "objects")
    bundle = _payload({native.oid: native}, filter_spec="blob:none")

    with pytest.raises(RuntimeError, match="Filtered Git bundles"):
        stage_git_bundle_import(store, bundle)

    assert _loose_files(store) == []


def test_non_sha1_remote_domain_is_rejected(tmp_path):
    native = _native("blob", b"payload")
    store = ObjectStore(tmp_path / "objects")
    bundle = _payload({native.oid: native}, object_format="sha256")

    with pytest.raises(RuntimeError, match="remote-native SHA-1"):
        stage_git_bundle_import(store, bundle)

    assert _loose_files(store) == []


def test_reference_target_must_exist_in_verified_graph(tmp_path):
    native = _native("blob", b"payload")
    store = ObjectStore(tmp_path / "objects")
    bundle = _payload(
        {native.oid: native},
        refs={"refs/heads/main": "56" * 20},
    )

    with pytest.raises(ValueError, match="absent from the verified graph"):
        stage_git_bundle_import(store, bundle)

    assert _loose_files(store) == []


def test_reference_names_are_revalidated_at_staging_boundary(tmp_path):
    native = _native("blob", b"payload")
    store = ObjectStore(tmp_path / "objects")
    bundle = _payload(
        {native.oid: native},
        refs={"refs/heads/bad..name": native.oid},
    )

    with pytest.raises(ValueError, match="Invalid staged Git bundle reference"):
        stage_git_bundle_import(store, bundle)

    assert _loose_files(store) == []


def test_publication_is_idempotent_for_existing_identical_objects(tmp_path):
    native = _native("blob", b"same")
    store = ObjectStore(tmp_path / "objects")
    bundle = _payload({native.oid: native})

    first = stage_git_bundle_import(store, bundle)
    before = {path.relative_to(store.root): path.read_bytes() for path in _loose_files(store)}
    second = stage_git_bundle_import(store, bundle)
    after = {path.relative_to(store.root): path.read_bytes() for path in _loose_files(store)}

    assert first == second
    assert before == after


def test_repository_metadata_is_untouched_by_bundle_object_staging(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    native = _native("blob", b"metadata-free")
    bundle = _payload({native.oid: native})

    head_path = repo.pygit_dir / "HEAD"
    config_path = repo.pygit_dir / "config.json"
    head_before = _path_snapshot(head_path)
    config_before = _path_snapshot(config_path)

    result = stage_git_bundle_import(repo.store, bundle)

    assert _path_snapshot(head_path) == head_before
    assert _path_snapshot(config_path) == config_before
    assert not any(path.is_file() for path in (repo.pygit_dir / "refs").rglob("*"))
    assert not (repo.pygit_dir / "shallow").exists()
    assert not (repo.pygit_dir / "promisor.json").exists()
    assert repo.store.read(result.ref_targets["refs/heads/main"]).data == b"metadata-free"


def test_native_git_bundle_crosses_sha1_to_sha256_content_boundary(tmp_path):
    native_repo = tmp_path / "native"
    subprocess.run(
        ["git", "init", "--object-format=sha1", "-b", "main", str(native_repo)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    subprocess.run(
        ["git", "-C", str(native_repo), "config", "user.name", "Bundle Test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(native_repo), "config", "user.email", "bundle@example.test"],
        check=True,
    )
    (native_repo / "hello.txt").write_text("hello from native git\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(native_repo), "add", "hello.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(native_repo), "commit", "-m", "bundle root"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    bundle_path = tmp_path / "full.bundle"
    subprocess.run(
        ["git", "-C", str(native_repo), "bundle", "create", str(bundle_path), "--all"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    payload = parse_git_bundle(bundle_path.read_bytes())
    assert payload.is_self_contained
    assert payload.objects is not None

    store = ObjectStore(tmp_path / "local-objects")
    staged = stage_git_bundle_import(store, payload)

    native_head = subprocess.check_output(
        ["git", "-C", str(native_repo), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    reachable = {
        line.split()[0]
        for line in subprocess.check_output(
            ["git", "-C", str(native_repo), "rev-list", "--objects", "--all"],
            text=True,
        ).splitlines()
        if line.strip()
    }

    assert reachable == set(payload.objects)
    assert set(staged.native_to_local) == reachable
    assert staged.ref_targets["refs/heads/main"] == staged.native_to_local[native_head]
    assert all(len(oid) == 40 for oid in staged.native_to_local)
    assert all(len(oid) == 64 for oid in staged.native_to_local.values())
    assert all(native != local for native, local in staged.native_to_local.items())
    assert all(store.read(oid) is not None for oid in staged.local_oids)

    local_commit = store.read(staged.native_to_local[native_head])
    assert isinstance(local_commit, CommitObject)
    assert "bundle root" in local_commit.message
