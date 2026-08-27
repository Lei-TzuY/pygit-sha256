"""Phase 148 path normalization regressions for checkout-index pipelines."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pygit import Repository


def _repo(tmp_path: Path) -> Repository:
    repo = Repository.init(str(tmp_path / "repo"))
    target = repo.worktree / "a.txt"
    target.write_text("alpha\n", encoding="utf-8")
    repo.add(["a.txt"])
    target.unlink()
    return repo


def test_nul_stdin_accepts_find_style_dot_slash_path(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "pygit", "checkout-index", "--stdin", "-z"],
        cwd=repo.worktree,
        input=b"./a.txt\0",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == b""
    assert (repo.worktree / "a.txt").read_bytes() == b"alpha\n"


def test_explicit_cli_accepts_dot_slash_path(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "pygit", "checkout-index", "./a.txt"],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (repo.worktree / "a.txt").read_text(encoding="utf-8") == "alpha\n"
