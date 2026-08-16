"""
tests/test_phase36.py
=====================
Phase 36 tests: commit --signoff / -s, diff --no-prefix, and rev-parse --verify.
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


# -- commit --signoff ------------------------------------------------------

class TestCommitSignoff:
    def test_commit_signoff(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        c1 = _commit_file(repo, "f1.txt", "v1\n", "Add feature", signoff=True)
        c_obj = repo._require_commit(c1)
        assert "Signed-off-by: Tester <t@e.com>" in c_obj.message


# -- diff --no-prefix ------------------------------------------------------

class TestDiffNoPrefix:
    def test_diff_no_prefix(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "f1.txt", "v1\n", "c1")
        (repo.worktree / "f1.txt").write_text("v2\n", encoding="utf-8")

        diff_out = repo.diff(no_prefix=True)
        assert "diff --pygit f1.txt f1.txt" in diff_out
        assert "--- f1.txt" in diff_out
        assert "+++ f1.txt" in diff_out
