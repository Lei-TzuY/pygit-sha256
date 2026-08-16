"""
tests/test_phase41.py
=====================
Phase 41 tests: diff --find-renames / -M.
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


# -- diff --find-renames ---------------------------------------------------

class TestDiffFindRenames:
    def test_diff_find_renames(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "f1.txt", "v1\n", "c1")
        # add modification
        (repo.worktree / "f1.txt").write_text("v2\n", encoding="utf-8")
        diff_out = repo.diff(find_renames=True)
        assert "diff --pygit" in diff_out
