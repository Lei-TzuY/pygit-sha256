from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

import pygit.protocol_v2_packfile_uri_incremental_fetch as phase338
from pygit.loose_object_map import publish_staged_loose_object_map
from pygit.objects.commit import CommitObject
from pygit.objects.tree import TreeObject
from pygit.protocol_v2_packfile_uri_batch import DownloadedPackfileUriBatch
from pygit.protocol_v2_packfile_uri_connectivity import certify_packfile_uri_roots
from pygit.protocol_v2_packfile_uri_incremental import (
    PackfileUriIncrementalState,
    plan_packfile_uri_incremental_state,
)
from pygit.protocol_v2_packfile_uri_refs import PackfileUriRefPublication
from pygit.protocol_v2_packfile_uri_stage import (
    StagedPackfileUriImport,
    stage_packfile_uri_import,
)
from pygit.protocol_v2_packfile_uri_tracking import PackfileUriRemoteTrackingPlan
from pygit.remote import NativeExporter, PackParser
from pygit.repo import Repository


EMPTY_BATCH = DownloadedPackfileUriBatch((), {}, 0)


def _mapped_tracking_tip(repo: Repository):
    tree = TreeObject()
    tree_oid = repo.store.write(tree)
    commit = CommitObject(tree=tree_oid, message="phase338 known-only\n")
    local_tip = repo.store.write(commit)

    exporter = NativeExporter(repo.store)
    native_tip = exporter.export_oid(local_tip)
    native_to_local = {
        native: local for local, native in exporter.converted.items()
    }
    staged = StagedPackfileUriImport(
        dict(sorted(native_to_local.items())),
        tuple(sorted(set(native_to_local.values()))),
    )
    published = publish_staged_loose_object_map(repo, staged)
    repo.refs.set_remote("origin", "main", local_tip)

    plan = PackfileUriRemoteTrackingPlan(
        expected_roots={native_tip: b"commit"},
        publications={
            "refs/remotes/origin/main": PackfileUriRefPublication(
                native_tip,
                local_tip,
            )
        },
        default_branch="main",
    )
    incremental = plan_packfile_uri_incremental_state(repo, plan)
    return local_tip, native_tip, native_to_local, published, plan, incremental


def test_empty_staging_still_rejects_without_known_objects(tmp_path: Path):
    repo = Repository.init(str(tmp_path / "repo"))

    with pytest.raises(ValueError, match="at least one native object"):
        stage_packfile_uri_import(repo.store, {}, EMPTY_BATCH)


def test_empty_staging_accepts_validated_known_objects_without_claiming_publication(
    tmp_path: Path,
):
    repo = Repository.init(str(tmp_path / "repo"))
    local_tip, native_tip, native_to_local, _, _, _ = _mapped_tracking_tip(repo)

    staged = stage_packfile_uri_import(
        repo.store,
        {},
        EMPTY_BATCH,
        known_native_to_local=native_to_local,
    )

    assert native_tip in native_to_local
    assert local_tip in native_to_local.values()
    assert staged.native_to_local == {}
    assert staged.local_oids == ()


def test_known_root_certification_requires_explicit_mapping_and_type(tmp_path: Path):
    repo = Repository.init(str(tmp_path / "repo"))
    local_tip, native_tip, native_to_local, _, _, _ = _mapped_tracking_tip(repo)
    staged = StagedPackfileUriImport({}, ())

    certificate = certify_packfile_uri_roots(
        repo.store,
        staged,
        {native_tip: b"commit"},
        known_native_to_local=native_to_local,
    )
    assert certificate.native_to_local == {native_tip: local_tip}
    assert certificate.expected_types == {native_tip: b"commit"}

    with pytest.raises(ValueError, match="staged import or known objects"):
        certify_packfile_uri_roots(repo.store, staged, {"f" * 40: b"commit"})

    with pytest.raises(ValueError, match="type mismatch"):
        certify_packfile_uri_roots(
            repo.store,
            staged,
            {native_tip: b"blob"},
            known_native_to_local=native_to_local,
        )


def test_known_only_transaction_skips_new_lmap_and_keeps_noop_ref_reflog_clean(
    tmp_path: Path,
    monkeypatch,
):
    repo = Repository.init(str(tmp_path / "repo"))
    local_tip, native_tip, _, existing_map, plan, incremental = _mapped_tracking_tip(repo)
    before_maps = sorted((repo.pygit_dir / "objects" / "object-map").glob("map-*.map"))
    before_reflog = repo.refs.read_reflog("refs/remotes/origin/main")

    def forbidden_map(*args, **kwargs):
        raise AssertionError("known-only completion must not publish an empty LMAP")

    monkeypatch.setattr(phase338, "publish_staged_loose_object_map", forbidden_map)

    result = phase338.execute_incremental_packfile_uri_fetch_transaction(
        repo,
        (),
        {},
        plan.expected_roots,
        plan.publications,
        incremental,
    )

    after_maps = sorted((repo.pygit_dir / "objects" / "object-map").glob("map-*.map"))
    assert result.staged == StagedPackfileUriImport({}, ())
    assert result.object_map is None
    assert result.certificate.native_to_local == {native_tip: local_tip}
    assert result.published_refs == {"refs/remotes/origin/main": local_tip}
    assert repo.refs.get_remote("origin", "main") == local_tip
    assert repo.refs.read_reflog("refs/remotes/origin/main") == before_reflog
    assert before_maps == after_maps == [existing_map.path]


def test_known_only_transaction_fails_closed_if_expected_root_is_not_known(tmp_path: Path):
    repo = Repository.init(str(tmp_path / "repo"))
    local_tip, native_tip, native_to_local, _, plan, incremental = _mapped_tracking_tip(repo)
    missing = "f" * 40
    bad_plan = PackfileUriRemoteTrackingPlan(
        expected_roots={missing: b"commit"},
        publications={
            "refs/remotes/origin/main": PackfileUriRefPublication(missing, local_tip)
        },
        default_branch="main",
    )

    with pytest.raises(ValueError, match="staged import or known objects"):
        phase338.execute_incremental_packfile_uri_fetch_transaction(
            repo,
            (),
            {},
            bad_plan.expected_roots,
            bad_plan.publications,
            PackfileUriIncrementalState(
                haves=incremental.haves,
                known_native_to_local=native_to_local,
                fallback_refs=(),
            ),
        )

    assert repo.refs.get_remote("origin", "main") == local_tip
    assert native_tip in incremental.haves


def test_native_git_can_produce_a_valid_zero_object_incremental_pack(tmp_path: Path):
    git = shutil.which("git")
    if git is None:
        pytest.skip("native git not installed")

    native_repo = tmp_path / "native"
    subprocess.run(
        [git, "init", str(native_repo)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    subprocess.run([git, "-C", str(native_repo), "config", "user.name", "Phase338"], check=True)
    subprocess.run(
        [git, "-C", str(native_repo), "config", "user.email", "phase338@example.test"],
        check=True,
    )
    (native_repo / "payload").write_text("already known\n", encoding="utf-8")
    subprocess.run([git, "-C", str(native_repo), "add", "payload"], check=True)
    subprocess.run(
        [git, "-C", str(native_repo), "commit", "-m", "known"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    tip = subprocess.check_output(
        [git, "-C", str(native_repo), "rev-parse", "HEAD"],
        text=True,
    ).strip()

    pack = subprocess.run(
        [git, "-C", str(native_repo), "pack-objects", "--stdout", "--revs"],
        input=f"{tip}\n^{tip}\n".encode(),
        check=True,
        stdout=subprocess.PIPE,
    ).stdout

    assert pack.startswith(b"PACK")
    assert int.from_bytes(pack[8:12], "big") == 0
    assert len(pack) == 32
    assert PackParser(pack).parse() == {}
