"""
tests/test_phase35.py
=====================
Phase 35 tests: commit --reset-author, diff --src-prefix/--dst-prefix, and rev_parse_namespaces pattern.
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


# -- commit --reset-author -------------------------------------------------

class TestCommitResetAuthor:
    def test_commit_reset_author(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        c1 = _commit_file(repo, "f1.txt", "v1\n", "c1", author="Original Author <orig@example.com>")

        (repo.worktree / "f1.txt").write_text("v2\n", encoding="utf-8")
        repo.add(["f1.txt"])

        c2 = repo.commit("amended", amend=True, reset_author=True, author_name="New Author", author_email="new@example.com")
        c_obj = repo._require_commit(c2)
        assert c_obj.author.name == "New Author"
        assert c_obj.author.email == "new@example.com"


# -- diff --src-prefix / --dst-prefix --------------------------------------

class TestDiffCustomPrefixes:
    def test_diff_custom_prefixes(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "f1.txt", "v1\n", "c1")
        (repo.worktree / "f1.txt").write_text("v2\n", encoding="utf-8")

        diff_out = repo.diff(src_prefix="i/", dst_prefix="w/")
        assert "diff --pygit i/f1.txt w/f1.txt" in diff_out
        assert "--- i/f1.txt" in diff_out
        assert "+++ w/f1.txt" in diff_out
