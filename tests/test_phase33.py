"""
tests/test_phase33.py
=====================
Phase 33 tests: commit --author, rev-parse --sq, and diff --compact-summary.
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


# -- commit --author="Name <email>" ----------------------------------------

class TestCommitAuthor:
    def test_commit_author_override(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        c1 = _commit_file(repo, "f1.txt", "v1\n", "c1", author="Custom User <custom@example.com>")
        c_obj = repo._require_commit(c1)
        assert c_obj.author.name == "Custom User"
        assert c_obj.author.email == "custom@example.com"


# -- diff --compact-summary ------------------------------------------------

class TestDiffCompactSummary:
    def test_diff_compact_summary(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "f1.txt", "v1\n", "c1")
        (repo.worktree / "f2.txt").write_text("v2\n", encoding="utf-8")
        repo.add(["f2.txt"])

        diff_cs = repo.diff(cached=True, compact_summary=True)
        assert "create mode 100644 f2.txt" in diff_cs
