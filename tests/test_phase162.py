"""Phase 162 tests: status config defaults and CLI precedence."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pygit import Repository
from pygit.status_config import (
    configured_ahead_behind,
    configured_show_stash,
    configured_untracked_mode,
    resolve_ahead_behind,
    resolve_show_stash,
    resolve_untracked_mode,
)


def _run(repo: Repository, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _write(repo: Repository, path: str, text: str = "x\n") -> None:
    target = repo.worktree / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _committed_repo(tmp_path: Path) -> Repository:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, "tracked.txt", "base\n")
    repo.add(["tracked.txt"])
    repo.commit("base", author_name="Tester", author_email="tester@example.com")
    return repo


def _ahead_repo(tmp_path: Path) -> Repository:
    repo = _committed_repo(tmp_path)
    base = repo.refs.resolve_head()
    assert base is not None
    repo.refs.set_remote("origin", "main", base)
    _write(repo, "tracked.txt", "base\nlocal\n")
    repo.add(["tracked.txt"])
    repo.commit("local", author_name="Tester", author_email="tester@example.com")
    return repo


def _stash_entries(repo: Repository, count: int) -> None:
    for index in range(count):
        repo.refs.set_stash(f"{index + 1:064x}", f"stash {index + 1}")


def test_status_config_defaults_match_git(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))

    assert configured_show_stash(repo) is False
    assert configured_untracked_mode(repo) == "normal"
    assert configured_ahead_behind(repo) is True


def test_show_stash_config_applies_to_long_and_porcelain_v2(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    _stash_entries(repo, 2)
    repo.config_set("status", "showStash", "true")

    long_status = _run(repo, "status")
    v2 = _run(repo, "status", "--porcelain=v2")
    short = _run(repo, "status", "--short")
    v1 = _run(repo, "status", "--porcelain=v1")

    assert long_status.returncode == 0, long_status.stderr
    assert "Your stash currently has 2 entries\n" in long_status.stdout
    assert v2.stdout == "# stash 2\n"
    assert short.stdout == ""
    assert v1.stdout == ""


def test_no_show_stash_overrides_config_true(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    _stash_entries(repo, 1)
    repo.config_set("status", "showStash", "yes")

    long_status = _run(repo, "status", "--no-show-stash")
    v2 = _run(repo, "status", "--porcelain=v2", "--no-show-stash")

    assert "Your stash currently has" not in long_status.stdout
    assert "# stash" not in v2.stdout


def test_show_stash_cli_overrides_config_false(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    _stash_entries(repo, 1)
    repo.config_set("status", "showStash", "false")

    result = _run(repo, "status", "--porcelain=v2", "--show-stash")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "# stash 1\n"


def test_show_untracked_files_no_and_all_config_modes(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, "scratch/a.txt")
    _write(repo, "scratch/deep/b.txt")

    repo.config_set("status", "showUntrackedFiles", "no")
    hidden = _run(repo, "status", "--porcelain=v1")
    assert hidden.returncode == 0, hidden.stderr
    assert hidden.stdout == ""

    repo.config_set("status", "showUntrackedFiles", "all")
    expanded = _run(repo, "status", "--porcelain=v1")
    assert expanded.stdout.splitlines() == [
        "?? scratch/a.txt",
        "?? scratch/deep/b.txt",
    ]


def test_show_untracked_files_boolean_spellings_match_git(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, "scratch/a.txt")

    repo.config_set("status", "showUntrackedFiles", "true")
    normal = _run(repo, "status", "--porcelain=v1")
    assert normal.stdout == "?? scratch/\n"

    repo.config_set("status", "showUntrackedFiles", "false")
    hidden = _run(repo, "status", "--porcelain=v1")
    assert hidden.stdout == ""


def test_untracked_cli_mode_overrides_config(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, "scratch/a.txt")
    repo.config_set("status", "showUntrackedFiles", "no")

    forced_all = _run(repo, "status", "--porcelain=v1", "-uall")
    assert forced_all.stdout == "?? scratch/a.txt\n"

    repo.config_set("status", "showUntrackedFiles", "all")
    forced_no = _run(repo, "status", "--porcelain=v1", "-uno")
    assert forced_no.stdout == ""


def test_ahead_behind_config_false_uses_nondetailed_long_and_short(tmp_path: Path) -> None:
    repo = _ahead_repo(tmp_path)
    repo.config_set("status", "aheadBehind", "false")

    long_status = _run(repo, "status")
    short = _run(repo, "status", "-sb")

    assert long_status.returncode == 0, long_status.stderr
    assert "Your branch and 'origin/main' refer to different commits." in long_status.stdout
    assert "ahead of 'origin/main'" not in long_status.stdout
    assert short.stdout.startswith("## main...origin/main [different]\n")


def test_explicit_ahead_behind_overrides_config_false(tmp_path: Path) -> None:
    repo = _ahead_repo(tmp_path)
    repo.config_set("status", "aheadBehind", "false")

    long_status = _run(repo, "status", "--ahead-behind")
    short = _run(repo, "status", "-sb", "--ahead-behind")

    assert "Your branch is ahead of 'origin/main' by 1 commit." in long_status.stdout
    assert short.stdout.startswith("## main...origin/main [ahead 1]\n")


def test_explicit_no_ahead_behind_overrides_config_true(tmp_path: Path) -> None:
    repo = _ahead_repo(tmp_path)
    repo.config_set("status", "aheadBehind", "true")

    short = _run(repo, "status", "-sb", "--no-ahead-behind")

    assert short.stdout.startswith("## main...origin/main [different]\n")


def test_porcelain_v1_ignores_ahead_behind_config_but_honors_cli(tmp_path: Path) -> None:
    repo = _ahead_repo(tmp_path)
    repo.config_set("status", "aheadBehind", "false")

    configured = _run(repo, "status", "--porcelain=v1", "-b")
    explicit = _run(repo, "status", "--porcelain=v1", "-b", "--no-ahead-behind")

    assert configured.stdout.startswith("## main...origin/main [ahead 1]\n")
    assert explicit.stdout.startswith("## main...origin/main [different]\n")


def test_porcelain_v2_ignores_config_and_uses_unknown_counts_for_cli_no(tmp_path: Path) -> None:
    repo = _ahead_repo(tmp_path)
    repo.config_set("status", "aheadBehind", "false")

    configured = _run(repo, "status", "--porcelain=v2", "--branch")
    explicit = _run(
        repo,
        "status",
        "--porcelain=v2",
        "--branch",
        "--no-ahead-behind",
    )

    assert "# branch.ab +1 -0\n" in configured.stdout
    assert "# branch.ab +? -?\n" in explicit.stdout


def test_resolvers_apply_cli_precedence_directly(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    repo.config_set("status", "showStash", "true")
    repo.config_set("status", "showUntrackedFiles", "all")
    repo.config_set("status", "aheadBehind", "false")

    assert resolve_show_stash(repo, False) is False
    assert resolve_untracked_mode(repo, "no") == "no"
    assert resolve_ahead_behind(repo, True, porcelain=False) is True
    assert resolve_ahead_behind(repo, None, porcelain=True) is True


def test_status_help_advertises_ahead_behind_controls(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))

    result = _run(repo, "status", "--help")

    assert result.returncode == 0
    assert "--ahead-behind" in result.stdout
    assert "--no-ahead-behind" in result.stdout
