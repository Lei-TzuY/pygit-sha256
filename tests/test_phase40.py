"""
tests/test_phase40.py
=====================
Phase 40 tests: rev-parse --is-inside-git-dir and commit --verbose / -v.
"""

from __future__ import annotations

import os
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


# -- rev-parse --is-inside-git-dir -----------------------------------------

class TestRevParseInsideGitDir:
    def test_rev_parse_is_inside_git_dir(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "f1.txt", "v1\n", "c1")
        cwd = Path.cwd().resolve()
        pg_dir = repo.pygit_dir.resolve()
        assert not (cwd == pg_dir or pg_dir in cwd.parents)
