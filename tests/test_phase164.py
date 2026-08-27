"""Phase 164 tests: branch/checkout/clone upstream tracking setup."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pygit import Repository
from pygit.tracking import configure_clone_tracking


def _run(repo: Repository, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _write(repo: Repository, path: str, text: str) -> None:
    target = repo.worktree / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _repo(tmp_path: Path) -> Repository:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, "tracked.txt", "base\n")
    repo.add(["tracked.txt"])
    repo.commit("base", author_name="Tester", author_email="tester@example.com")
    return repo


def _remote(repo: Repository, remote: str, branch: str) -> str:
    oid = repo.refs.resolve_head()
    assert oid is not None
    repo.refs.set_remote(remote, branch, oid)
    return oid


def _assert_tracking(repo: Repository, branch: str, remote: str, merge_branch: str) -> None:
    assert repo.config_get("branch", f"{branch}.remote") == remote
    assert repo.config_get("branch", f"{branch}.merge") == f"refs/heads/{merge_branch}"


def test_branch_short_track_sets_remote_upstream(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    oid = _remote(repo, "origin", "feature")

    result = _run(repo, "branch", "-t", "topic", "origin/feature")

    assert result.returncode == 0, result.stderr
    assert repo.refs.get_branch("topic") == oid
    _assert_tracking(repo, "topic", "origin", "feature")


def test_branch_bare_long_track_does_not_consume_branch_name(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _remote(repo, "origin", "feature")

    result = _run(repo, "branch", "--track", "topic", "origin/feature")

    assert result.returncode == 0, result.stderr
    _assert_tracking(repo, "topic", "origin", "feature")


def test_branch_auto_setup_merge_tracks_remote_start_point_by_default(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _remote(repo, "origin", "feature")

    result = _run(repo, "branch", "topic", "origin/feature")

    assert result.returncode == 0, result.stderr
    _assert_tracking(repo, "topic", "origin", "feature")


def test_branch_auto_setup_merge_false_and_no_track_suppress_tracking(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _remote(repo, "origin", "feature")
    repo.config_set("branch", "autoSetupMerge", "false")

    result = _run(repo, "branch", "auto-off", "origin/feature")
    forced = _run(repo, "branch", "--no-track", "explicit-off", "origin/feature")

    assert result.returncode == 0, result.stderr
    assert forced.returncode == 0, forced.stderr
    assert repo.config_get("branch", "auto-off.remote") is None
    assert repo.config_get("branch", "explicit-off.remote") is None


def test_branch_track_direct_can_track_local_branch(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    result = _run(repo, "branch", "--track=direct", "topic", "main")

    assert result.returncode == 0, result.stderr
    _assert_tracking(repo, "topic", ".", "main")


def test_branch_track_inherit_copies_existing_upstream(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _remote(repo, "origin", "feature")
    repo.branch("base")
    repo.config_set("branch", "base.remote", "origin")
    repo.config_set("branch", "base.merge", "refs/heads/feature")

    result = _run(repo, "branch", "--track=inherit", "topic", "base")

    assert result.returncode == 0, result.stderr
    _assert_tracking(repo, "topic", "origin", "feature")


def test_set_and_unset_upstream_to(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _remote(repo, "backup", "release")
    repo.branch("topic")

    configured = _run(repo, "branch", "--set-upstream-to=backup/release", "topic")
    assert configured.returncode == 0, configured.stderr
    _assert_tracking(repo, "topic", "backup", "release")

    removed = _run(repo, "branch", "--unset-upstream", "topic")
    assert removed.returncode == 0, removed.stderr
    assert repo.config_get("branch", "topic.remote") is None
    assert repo.config_get("branch", "topic.merge") is None


def test_branch_rename_moves_tracking_configuration(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _remote(repo, "origin", "feature")
    repo.branch("old", start_point="origin/feature")
    repo.config_set("branch", "old.remote", "origin")
    repo.config_set("branch", "old.merge", "refs/heads/feature")

    result = _run(repo, "branch", "-m", "old", "new")

    assert result.returncode == 0, result.stderr
    _assert_tracking(repo, "new", "origin", "feature")
    assert repo.config_get("branch", "old.remote") is None
    assert repo.config_get("branch", "old.merge") is None


def test_checkout_b_from_remote_tracks_by_default(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    oid = _remote(repo, "origin", "feature")

    result = _run(repo, "checkout", "-b", "topic", "origin/feature")

    assert result.returncode == 0, result.stderr
    assert repo.refs.current_branch() == "topic"
    assert repo.refs.get_branch("topic") == oid
    _assert_tracking(repo, "topic", "origin", "feature")


def test_checkout_b_no_track_suppresses_auto_setup(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _remote(repo, "origin", "feature")

    result = _run(repo, "checkout", "--no-track", "-b", "topic", "origin/feature")

    assert result.returncode == 0, result.stderr
    assert repo.refs.current_branch() == "topic"
    assert repo.config_get("branch", "topic.remote") is None


def test_checkout_track_without_b_derives_local_branch_name(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _remote(repo, "origin", "feature")

    result = _run(repo, "checkout", "--track", "origin/feature")

    assert result.returncode == 0, result.stderr
    assert repo.refs.current_branch() == "feature"
    _assert_tracking(repo, "feature", "origin", "feature")


def test_checkout_plain_name_guesses_unique_remote_tracking_branch(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _remote(repo, "origin", "feature")

    result = _run(repo, "checkout", "feature")

    assert result.returncode == 0, result.stderr
    assert repo.refs.current_branch() == "feature"
    _assert_tracking(repo, "feature", "origin", "feature")


def test_checkout_ambiguous_remote_branch_uses_default_remote(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _remote(repo, "origin", "feature")
    _remote(repo, "backup", "feature")

    ambiguous = _run(repo, "checkout", "feature")
    assert ambiguous.returncode == 1
    assert "multiple remote-tracking branches" in ambiguous.stderr
    assert repo.refs.get_branch("feature") is None

    repo.config_set("checkout", "defaultRemote", "backup")
    chosen = _run(repo, "checkout", "feature")
    assert chosen.returncode == 0, chosen.stderr
    assert repo.refs.current_branch() == "feature"
    _assert_tracking(repo, "feature", "backup", "feature")


def test_clone_tracking_helper_writes_native_style_config(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _remote(repo, "origin", "main")

    configure_clone_tracking(repo, "main")

    _assert_tracking(repo, "main", "origin", "main")


def test_branch_and_checkout_help_advertise_tracking_controls(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    branch_help = _run(repo, "branch", "--help")
    checkout_help = _run(repo, "checkout", "--help")

    assert branch_help.returncode == 0
    assert "--track" in branch_help.stdout
    assert "--set-upstream-to" in branch_help.stdout
    assert "--unset-upstream" in branch_help.stdout
    assert checkout_help.returncode == 0
    assert "--track" in checkout_help.stdout
    assert "--no-track" in checkout_help.stdout
