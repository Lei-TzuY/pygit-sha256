"""Phase 155 tests: Git-style status --ignored mode presentation."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pygit import Repository
from pygit.ignore import IgnoreMatcher


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


def _ignored_fixture(tmp_path: Path) -> Repository:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, ".pygitignore", "build/\nreports/*.log\n*.tmp\n")
    _commit(repo, ".pygitignore")
    _write(repo, "build/a.txt")
    _write(repo, "build/deep/b.txt")
    _write(repo, "reports/a.log")
    _write(repo, "reports/deep/b.log")
    _write(repo, "cache.tmp")
    _write(repo, "visible.txt")
    return repo


def test_ignore_matcher_distinguishes_inherited_directory_match(tmp_path: Path) -> None:
    repo = _ignored_fixture(tmp_path)
    matcher = IgnoreMatcher(repo.worktree)

    assert matcher.is_ignored("build/a.txt")
    assert matcher.is_explicitly_ignored("build", is_dir=True)
    assert not matcher.is_explicitly_ignored("build/a.txt")


def test_bare_ignored_keeps_traditional_mode(tmp_path: Path) -> None:
    repo = _ignored_fixture(tmp_path)

    result = _run(repo, "status", "--porcelain=v1", "--ignored")

    assert result.returncode == 0, result.stderr
    assert "!! build/" in result.stdout.splitlines()
    assert "!! cache.tmp" in result.stdout.splitlines()
    assert "?? visible.txt" in result.stdout.splitlines()
    assert "!! build/a.txt" not in result.stdout


def test_ignored_matching_emits_direct_directory_not_descendants(tmp_path: Path) -> None:
    repo = _ignored_fixture(tmp_path)

    result = _run(repo, "status", "--porcelain=v1", "--ignored=matching")

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert "!! build/" in lines
    assert "!! build/a.txt" not in lines
    assert "!! build/deep/b.txt" not in lines


def test_ignored_matching_expands_contents_when_directory_did_not_match(tmp_path: Path) -> None:
    repo = _ignored_fixture(tmp_path)

    result = _run(repo, "status", "--porcelain=v1", "--ignored=matching")

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert "!! reports/a.log" in lines
    assert "!! reports/" not in lines
    assert "!! cache.tmp" in lines


def test_ignored_matching_is_independent_of_untracked_all(tmp_path: Path) -> None:
    repo = _ignored_fixture(tmp_path)

    normal = _run(repo, "status", "--porcelain=v1", "--ignored=matching")
    all_files = _run(repo, "status", "--porcelain=v1", "--ignored=matching", "-uall")

    assert normal.returncode == 0, normal.stderr
    assert all_files.returncode == 0, all_files.stderr
    normal_ignored = [line for line in normal.stdout.splitlines() if line.startswith("!!")]
    all_ignored = [line for line in all_files.stdout.splitlines() if line.startswith("!!")]
    assert normal_ignored == all_ignored


def test_ignored_no_suppresses_ignored_but_preserves_untracked(tmp_path: Path) -> None:
    repo = _ignored_fixture(tmp_path)

    result = _run(repo, "status", "--porcelain=v1", "--ignored=no")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "?? visible.txt\n"


def test_porcelain_v2_threads_matching_mode(tmp_path: Path) -> None:
    repo = _ignored_fixture(tmp_path)

    result = _run(repo, "status", "--porcelain=v2", "--ignored=matching")

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert "! build/" in lines
    assert "! reports/a.log" in lines
    assert "! build/a.txt" not in lines


def test_matching_mode_preserves_nul_framing(tmp_path: Path) -> None:
    repo = _ignored_fixture(tmp_path)

    result = _run(repo, "status", "--porcelain=v1", "--ignored=matching", "-z")

    assert result.returncode == 0, result.stderr
    records = [record for record in result.stdout.split("\0") if record]
    assert "!! build/" in records
    assert "!! reports/a.log" in records


def test_long_status_matching_uses_matching_paths(tmp_path: Path) -> None:
    repo = _ignored_fixture(tmp_path)

    result = _run(repo, "status", "--ignored=matching")

    assert result.returncode == 0, result.stderr
    assert "Ignored files:" in result.stdout
    assert "\tbuild/\n" in result.stdout
    assert "\treports/a.log\n" in result.stdout
    assert "build/a.txt" not in result.stdout


def test_status_help_documents_ignored_modes(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))

    result = _run(repo, "status", "--help")

    assert result.returncode == 0
    normalized = " ".join(result.stdout.split())
    assert "--ignored [{traditional,matching,no}]" in normalized
