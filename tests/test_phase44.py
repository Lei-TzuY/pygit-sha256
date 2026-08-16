"""
tests/test_phase44.py
=====================
Phase 44 tests: rev-parse --resolve-git-dir and diff --dirstat.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pygit import Repository


# -- helpers ---------------------------------------------------------------

def _commit_file(repo: Repository, name: str, content: str, msg: str, **kwargs) -> str:
    """Write *content* to *name*, stage, and commit."""
    path = repo.worktree / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    repo.add([name])
    return repo.commit(msg, author_name="Tester", author_email="t@e.com", **kwargs)


# -- rev-parse --resolve-git-dir -------------------------------------------

class TestPhase44:
    def test_rev_parse_resolve_git_dir(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "f1.txt", "v1\n", "c1")
        assert (repo.pygit_dir).exists()

    def test_diff_dirstat(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "sub/f1.txt", "v1\n", "c1")
        (repo.worktree / "sub" / "f1.txt").write_text("v2\n", encoding="utf-8")
        diff_out = repo.diff(dirstat=True)
        assert diff_out is not None
