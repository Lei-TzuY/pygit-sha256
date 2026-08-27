"""Phase 154 tests: Git-style status untracked-file presentation modes."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pygit import Repository


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


def _commit(repo: Repository, *paths: str) -> None:
    repo.add(list(paths))
    repo.commit("base", author_name="Tester", author_email="tester@example.com")


def test_default_normal_collapses_pure_untracked_directory(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, "scratch/a.txt")
    _write(repo, "scratch/deep/b.txt")
    _write(repo, "root.txt")

    result = _run(repo, "status", "--porcelain=v1")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["?? root.txt", "?? scratch/"]


def test_untracked_all_and_bare_u_show_individual_files(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, "scratch/a.txt")
    _write(repo, "scratch/deep/b.txt")

    explicit = _run(repo, "status", "--porcelain=v1", "--untracked-files=all")
    short = _run(repo, "status", "--porcelain=v1", "-u")

    expected = ["?? scratch/a.txt", "?? scratch/deep/b.txt"]
    assert explicit.returncode == 0, explicit.stderr
    assert explicit.stdout.splitlines() == expected
    assert short.stdout.splitlines() == expected


def test_short_attached_untracked_mode_spelling(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, "scratch/a.txt")

    result = _run(repo, "status", "--porcelain=v1", "-uno")

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_untracked_no_hides_only_untracked_state(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, "tracked.txt", "old\n")
    _commit(repo, "tracked.txt")
    _write(repo, "tracked.txt", "new\n")
    _write(repo, "scratch/a.txt")

    result = _run(repo, "status", "--porcelain=v1", "--untracked-files=no")

    assert result.returncode == 0, result.stderr
    assert result.stdout == " M tracked.txt\n"


def test_normal_does_not_hide_untracked_paths_beside_tracked_content(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, "mixed/tracked.txt")
    _commit(repo, "mixed/tracked.txt")
    _write(repo, "mixed/loose.txt")
    _write(repo, "mixed/newdir/a.txt")
    _write(repo, "mixed/newdir/deep/b.txt")

    result = _run(repo, "status", "--porcelain=v1")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "?? mixed/loose.txt",
        "?? mixed/newdir/",
    ]


def test_porcelain_v2_uses_same_normal_and_all_modes(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, "scratch/a.txt")
    _write(repo, "scratch/b.txt")

    normal = _run(repo, "status", "--porcelain=v2")
    all_files = _run(repo, "status", "--porcelain=v2", "-uall")

    assert normal.returncode == 0, normal.stderr
    assert normal.stdout == "? scratch/\n"
    assert all_files.stdout.splitlines() == ["? scratch/a.txt", "? scratch/b.txt"]


def test_nul_mode_preserves_collapsed_directory_record(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, "scratch/a.txt")

    result = _run(repo, "status", "-z")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "?? scratch/\0"


def test_ignored_traditional_collapses_unless_untracked_all(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, ".pygitignore", "ignored/\n")
    _commit(repo, ".pygitignore")
    _write(repo, "ignored/a.txt")
    _write(repo, "ignored/deep/b.txt")

    normal = _run(repo, "status", "--porcelain=v1", "--ignored")
    all_files = _run(repo, "status", "--porcelain=v1", "--ignored", "-uall")

    assert normal.returncode == 0, normal.stderr
    assert normal.stdout == "!! ignored/\n"
    assert all_files.stdout.splitlines() == ["!! ignored/a.txt", "!! ignored/deep/b.txt"]


def test_long_status_default_reports_collapsed_directory(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, "scratch/a.txt")

    result = _run(repo, "status")

    assert result.returncode == 0, result.stderr
    assert "Untracked files:" in result.stdout
    assert "\tscratch/\n" in result.stdout
    assert "scratch/a.txt" not in result.stdout


def test_status_help_documents_untracked_modes(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))

    result = _run(repo, "status", "--help")

    assert result.returncode == 0
    normalized = " ".join(result.stdout.split())
    assert "-u [{no,normal,all}]" in normalized
    assert "--untracked-files [{no,normal,all}]" in normalized
