from pathlib import Path

import pytest

from pygit.protocol_v2_packfile_uri_tracking import (
    PackfileUriRemoteTrackingPlan,
    plan_packfile_uri_remote_tracking_publication,
)
from pygit.refs import ZERO_SHA
from pygit.remote import Advertisement
from pygit.repo import Repository


OID_MAIN = "1" * 40
OID_FEATURE = "2" * 40
OID_SHARED = "3" * 40
LOCAL_OLD = "a" * 64


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def _advertisement(*, shared: bool = False) -> Advertisement:
    feature = OID_MAIN if shared else OID_FEATURE
    return Advertisement(
        refs={
            "HEAD": OID_MAIN,
            "refs/heads/main": OID_MAIN,
            "refs/heads/feature/topic": feature,
            "refs/tags/v1": "4" * 40,
            "refs/tags/v1^{}": OID_MAIN,
        },
        capabilities=set(),
        symrefs={"HEAD": "refs/heads/main"},
    )


def test_plans_all_advertised_branches_as_remote_tracking_cas(tmp_path):
    repo = _repo(tmp_path)

    plan = plan_packfile_uri_remote_tracking_publication(repo, _advertisement())

    assert isinstance(plan, PackfileUriRemoteTrackingPlan)
    assert plan.expected_roots == {OID_MAIN: b"commit", OID_FEATURE: b"commit"}
    assert set(plan.publications) == {
        "refs/remotes/origin/main",
        "refs/remotes/origin/feature/topic",
    }
    assert plan.publications["refs/remotes/origin/main"].native_oid == OID_MAIN
    assert plan.publications["refs/remotes/origin/main"].old_local_oid == ZERO_SHA
    assert plan.default_branch == "main"


def test_existing_tracking_ref_becomes_expected_old_sha256(tmp_path):
    repo = _repo(tmp_path)
    repo.refs.set_remote("origin", "main", LOCAL_OLD)

    plan = plan_packfile_uri_remote_tracking_publication(
        repo,
        _advertisement(),
        branches=["refs/heads/main"],
    )

    publication = plan.publications["refs/remotes/origin/main"]
    assert publication.old_local_oid == LOCAL_OLD
    assert len(publication.old_local_oid) == 64
    assert publication.native_oid == OID_MAIN
    assert len(publication.native_oid) == 40


def test_shared_native_tip_deduplicates_expected_root_but_not_publications(tmp_path):
    repo = _repo(tmp_path)
    advertisement = _advertisement(shared=True)

    plan = plan_packfile_uri_remote_tracking_publication(repo, advertisement)

    assert plan.expected_roots == {OID_MAIN: b"commit"}
    assert set(plan.publications) == {
        "refs/remotes/origin/main",
        "refs/remotes/origin/feature/topic",
    }
    assert all(p.native_oid == OID_MAIN for p in plan.publications.values())


def test_explicit_branch_selection_preserves_scope_and_remote_name(tmp_path):
    repo = _repo(tmp_path)

    plan = plan_packfile_uri_remote_tracking_publication(
        repo,
        _advertisement(),
        remote="upstream/team",
        branches=["refs/heads/feature/topic"],
    )

    assert plan.expected_roots == {OID_FEATURE: b"commit"}
    assert set(plan.publications) == {"refs/remotes/upstream/team/feature/topic"}
    assert plan.default_branch is None


def test_tags_and_peeled_tag_records_are_not_publication_targets(tmp_path):
    repo = _repo(tmp_path)

    plan = plan_packfile_uri_remote_tracking_publication(repo, _advertisement())

    assert all("refs/tags/" not in refname for refname in plan.publications)
    assert "4" * 40 not in plan.expected_roots


def test_missing_or_duplicate_selected_branch_is_rejected(tmp_path):
    repo = _repo(tmp_path)
    advertisement = _advertisement()

    with pytest.raises(ValueError, match="not advertised"):
        plan_packfile_uri_remote_tracking_publication(
            repo,
            advertisement,
            branches=["refs/heads/missing"],
        )

    with pytest.raises(ValueError, match="duplicate"):
        plan_packfile_uri_remote_tracking_publication(
            repo,
            advertisement,
            branches=["refs/heads/main", "refs/heads/main"],
        )


def test_invalid_native_branch_oid_fails_closed(tmp_path):
    repo = _repo(tmp_path)
    advertisement = Advertisement(
        refs={"refs/heads/main": "f" * 39},
        capabilities=set(),
        symrefs={},
    )

    with pytest.raises(ValueError, match="remote-native SHA-1"):
        plan_packfile_uri_remote_tracking_publication(repo, advertisement)

    assert repo.refs.get_remote("origin", "main") is None


def test_invalid_remote_or_branch_selection_shape_is_rejected(tmp_path):
    repo = _repo(tmp_path)
    advertisement = _advertisement()

    with pytest.raises((ValueError, TypeError)):
        plan_packfile_uri_remote_tracking_publication(repo, advertisement, remote="../evil")

    with pytest.raises(TypeError, match="iterable"):
        plan_packfile_uri_remote_tracking_publication(
            repo,
            advertisement,
            branches="refs/heads/main",
        )

    with pytest.raises(ValueError, match="full refs/heads"):
        plan_packfile_uri_remote_tracking_publication(
            repo,
            advertisement,
            branches=["main"],
        )


def test_planner_is_read_only(tmp_path):
    repo = _repo(tmp_path)
    before_head = repo.refs.get_head()

    plan_packfile_uri_remote_tracking_publication(repo, _advertisement())

    assert repo.refs.get_head() == before_head
    assert repo.refs.list_remotes("origin") == []
    assert not (repo.pygit_dir / "logs").exists()
