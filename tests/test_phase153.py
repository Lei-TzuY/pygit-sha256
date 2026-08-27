"""Phase 153 tests: porcelain-v1 NUL implication and pathname quoting."""

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


def test_z_without_explicit_porcelain_implies_v1(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, "new.txt")

    implicit = _run(repo, "status", "-z")
    explicit = _run(repo, "status", "--porcelain=v1", "-z")

    assert implicit.returncode == 0, implicit.stderr
    assert implicit.stderr == ""
    assert implicit.stdout == explicit.stdout == "?? new.txt\0"


def test_z_preserves_raw_newline_backslash_and_quote_path(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    path = 'odd\nname\\".txt'
    _write(repo, path)

    result = _run(repo, "status", "-z")

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"?? {path}\0"


def test_porcelain_v1_quotes_ambiguous_path_without_z(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    path = 'odd\nname\\".txt'
    _write(repo, path)

    result = _run(repo, "status", "--porcelain=v1")

    assert result.returncode == 0, result.stderr
    assert result.stdout == '?? "odd\\nname\\\\\\\".txt"\n'


def test_short_format_uses_same_safe_path_quoting(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, "line\nbreak.txt")

    result = _run(repo, "status", "--short")

    assert result.returncode == 0, result.stderr
    assert result.stdout == '?? "line\\nbreak.txt"\n'


def test_plain_ascii_paths_remain_unquoted(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, "ordinary.txt")

    short = _run(repo, "status", "--short")
    porcelain = _run(repo, "status", "--porcelain=v1")

    assert short.stdout == porcelain.stdout == "?? ordinary.txt\n"


def test_z_branch_header_and_records_share_nul_framing(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, "new.txt")

    result = _run(repo, "status", "-z", "--branch")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "## main\0?? new.txt\0"


def test_z_with_explicit_porcelain_v2_keeps_v2_protocol(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, "new.txt")

    result = _run(repo, "status", "--porcelain=v2", "-z")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "? new.txt\0"


def test_status_help_documents_z_implication(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))

    result = _run(repo, "status", "--help")

    assert result.returncode == 0
    normalized = " ".join(result.stdout.split())
    assert "implies --porcelain=v1" in normalized
