"""Phase 163 tests: branch-configured status upstream tracking."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pygit import Repository
from pygit.status_upstream import configured_upstream, resolve_status_upstream


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


def _base_repo(tmp_path: Path) -> tuple[Repository, str]:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, "tracked.txt", "base\n")
    repo.add(["tracked.txt"])
    base = repo.commit("base", author_name="Tester", author_email="tester@example.com")
    return repo, base


def _commit_local(repo: Repository) -> str:
    _write(repo, "tracked.txt", "base\nlocal\n")
    repo.add(["tracked.txt"])
    return repo.commit("local", author_name="Tester", author_email="tester@example.com")


def _track(repo: Repository, remote: str, branch: str) -> None:
    current = repo.refs.current_branch()
    assert current is not None
    repo.config_set("branch", f"{current}.remote", remote)
    repo.config_set("branch", f"{current}.merge", f"refs/heads/{branch}")


def test_configured_remote_and_merge_override_legacy_origin_current(tmp_path: Path) -> None:
    repo, base = _base_repo(tmp_path)
    repo.refs.set_remote("backup", "release", base)
    head = _commit_local(repo)
    # The legacy origin/current heuristic says up-to-date.  Configured tracking
    # deliberately points elsewhere and must win.
    repo.refs.set_remote("origin", "main", head)
    _track(repo, "backup", "release")

    short = _run(repo, "status", "-sb")
    long_status = _run(repo, "status")

    assert short.returncode == 0, short.stderr
    assert short.stdout.splitlines()[0] == "## main...backup/release [ahead 1]"
    assert "origin/main" not in short.stdout
    assert "Your branch is ahead of 'backup/release' by 1 commit." in long_status.stdout


def test_porcelain_v2_uses_configured_upstream_name_and_counts(tmp_path: Path) -> None:
    repo, base = _base_repo(tmp_path)
    repo.refs.set_remote("backup", "release", base)
    head = _commit_local(repo)
    _track(repo, "backup", "release")

    result = _run(repo, "status", "--porcelain=v2", "--branch")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[:4] == [
        f"# branch.oid {head}",
        "# branch.head main",
        "# branch.upstream backup/release",
        "# branch.ab +1 -0",
    ]


def test_dot_remote_tracks_a_local_branch(tmp_path: Path) -> None:
    repo, base = _base_repo(tmp_path)
    repo.refs.set_branch("integration", base)
    _commit_local(repo)
    _track(repo, ".", "integration")

    short = _run(repo, "status", "-sb")
    v2 = _run(repo, "status", "--porcelain=v2", "--branch")

    assert short.stdout.splitlines()[0] == "## main...integration [ahead 1]"
    assert "# branch.upstream integration\n" in v2.stdout
    assert "# branch.ab +1 -0\n" in v2.stdout


def test_missing_configured_tracking_ref_reports_gone(tmp_path: Path) -> None:
    repo, _base = _base_repo(tmp_path)
    _track(repo, "backup", "release")

    short = _run(repo, "status", "-sb")
    long_status = _run(repo, "status")
    v2 = _run(repo, "status", "--porcelain=v2", "--branch")

    assert short.returncode == 0, short.stderr
    assert short.stdout.splitlines()[0] == "## main...backup/release [gone]"
    assert "Your branch is based on 'backup/release', but the upstream is gone." in long_status.stdout
    assert "# branch.upstream backup/release\n" in v2.stdout
    assert "# branch.ab " not in v2.stdout


def test_gone_state_beats_no_ahead_behind_unknown_count_marker(tmp_path: Path) -> None:
    repo, _base = _base_repo(tmp_path)
    _track(repo, "backup", "release")

    short = _run(repo, "status", "-sb", "--no-ahead-behind")
    v2 = _run(
        repo,
        "status",
        "--porcelain=v2",
        "--branch",
        "--no-ahead-behind",
    )

    assert short.stdout.splitlines()[0] == "## main...backup/release [gone]"
    assert "# branch.upstream backup/release\n" in v2.stdout
    assert "# branch.ab +? -?" not in v2.stdout


def test_partial_tracking_config_suppresses_legacy_fallback(tmp_path: Path) -> None:
    repo, base = _base_repo(tmp_path)
    repo.refs.set_remote("origin", "main", base)
    repo.config_set("branch", "main.remote", "backup")

    short = _run(repo, "status", "-sb")
    v2 = _run(repo, "status", "--porcelain=v2", "--branch")

    assert short.stdout.splitlines()[0] == "## main"
    assert "origin/main" not in short.stdout
    assert "# branch.upstream" not in v2.stdout


def test_no_tracking_config_keeps_phase150_legacy_fallback(tmp_path: Path) -> None:
    repo, base = _base_repo(tmp_path)
    repo.refs.set_remote("origin", "main", base)
    _commit_local(repo)

    short = _run(repo, "status", "-sb")

    assert short.stdout.splitlines()[0] == "## main...origin/main [ahead 1]"


def test_configured_upstream_parser_accepts_full_and_simple_merge_names(tmp_path: Path) -> None:
    repo, _base = _base_repo(tmp_path)
    repo.config_set("branch", "main.remote", "backup")
    repo.config_set("branch", "main.merge", "refs/heads/release")

    spec, configured = configured_upstream(repo, "main")
    assert configured is True
    assert spec is not None
    assert spec.display == "backup/release"

    repo.config_set("branch", "main.merge", "release")
    simple, configured = configured_upstream(repo, "main")
    assert configured is True
    assert simple is not None
    assert simple.display == "backup/release"


def test_resolver_metadata_exposes_remote_branch_and_gone(tmp_path: Path) -> None:
    repo, base = _base_repo(tmp_path)
    repo.refs.set_remote("mirror", "stable", base)
    _track(repo, "mirror", "stable")

    resolved = resolve_status_upstream(repo)
    assert resolved == {
        "upstream": "mirror/stable",
        "ahead": 0,
        "behind": 0,
        "gone": False,
        "remote": "mirror",
        "branch": "stable",
    }

    repo.refs.delete_remote("mirror", "stable")
    gone = resolve_status_upstream(repo)
    assert gone is not None
    assert gone["upstream"] == "mirror/stable"
    assert gone["gone"] is True


def test_detached_head_does_not_apply_branch_tracking_config(tmp_path: Path) -> None:
    repo, base = _base_repo(tmp_path)
    _track(repo, "backup", "release")
    repo.refs.set_head_detached(base)

    assert resolve_status_upstream(repo, {"upstream": "origin/main"}) is None
