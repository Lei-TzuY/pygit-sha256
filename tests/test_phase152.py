"""Phase 152 tests: reflog-backed ``status --show-stash`` reporting."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pygit import Repository
from pygit.status_porcelain_v2 import stash_count


def _run(repo: Repository, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _stash_entries(repo: Repository, count: int) -> None:
    for index in range(count):
        oid = f"{index + 1:064x}"
        repo.refs.set_stash(oid, f"stash {index + 1}")


def test_stash_count_uses_refs_stash_reflog(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    assert stash_count(repo) == 0

    _stash_entries(repo, 2)

    assert stash_count(repo) == 2


def test_porcelain_v2_show_stash_emits_optional_header_without_branch(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    _stash_entries(repo, 2)

    result = _run(repo, "status", "--porcelain=v2", "--show-stash")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "# stash 2\n"


def test_porcelain_v2_stash_header_follows_branch_headers(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    _stash_entries(repo, 1)

    result = _run(repo, "status", "--porcelain=v2", "--branch", "--show-stash")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "# branch.oid (initial)",
        "# branch.head main",
        "# stash 1",
    ]


def test_porcelain_v2_show_stash_omits_zero_count(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))

    result = _run(repo, "status", "--porcelain=v2", "--show-stash")

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_porcelain_v2_z_nul_terminates_stash_header(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    _stash_entries(repo, 2)

    result = _run(repo, "status", "--porcelain=v2", "--show-stash", "-z")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "# stash 2\0"


def test_long_status_show_stash_uses_native_singular_and_plural(tmp_path: Path) -> None:
    one = Repository.init(str(tmp_path / "one"))
    _stash_entries(one, 1)
    singular = _run(one, "status", "--show-stash")
    assert singular.returncode == 0, singular.stderr
    assert "Your stash currently has 1 entry\n" in singular.stdout

    two = Repository.init(str(tmp_path / "two"))
    _stash_entries(two, 2)
    plural = _run(two, "status", "--show-stash")
    assert plural.returncode == 0, plural.stderr
    assert "Your stash currently has 2 entries\n" in plural.stdout


def test_no_show_stash_last_option_wins_like_git_boolean_options(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    _stash_entries(repo, 1)

    suppressed = _run(
        repo,
        "status",
        "--porcelain=v2",
        "--show-stash",
        "--no-show-stash",
    )
    enabled = _run(
        repo,
        "status",
        "--porcelain=v2",
        "--no-show-stash",
        "--show-stash",
    )

    assert suppressed.returncode == 0
    assert suppressed.stdout == ""
    assert enabled.returncode == 0
    assert enabled.stdout == "# stash 1\n"


def test_short_and_porcelain_v1_do_not_gain_stash_records(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    _stash_entries(repo, 1)

    short = _run(repo, "status", "--short", "--show-stash")
    v1 = _run(repo, "status", "--porcelain=v1", "--show-stash")

    assert short.returncode == 0
    assert short.stdout == ""
    assert v1.returncode == 0
    assert v1.stdout == ""


def test_status_help_advertises_stash_controls(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))

    result = _run(repo, "status", "--help")

    assert result.returncode == 0
    assert "--show-stash" in result.stdout
    assert "--no-show-stash" in result.stdout
