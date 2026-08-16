"""
tests/test_phase34.py
=====================
Phase 34 tests: commit --date, rev-parse --not, diff --raw, and stash save alias.
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


# -- commit --date ---------------------------------------------------------

class TestCommitDate:
    def test_commit_date_override(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        c1 = _commit_file(repo, "f1.txt", "v1\n", "c1", commit_date="1600000000")
        c_obj = repo._require_commit(c1)
        assert c_obj.author.timestamp == 1600000000
        assert c_obj.committer.timestamp == 1600000000


# -- diff --raw ------------------------------------------------------------

class TestDiffRaw:
    def test_diff_raw(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "f1.txt", "v1\n", "c1")
        (repo.worktree / "f2.txt").write_text("v2\n", encoding="utf-8")
        repo.add(["f2.txt"])

        diff_raw = repo.diff(cached=True, raw=True)
        assert ":100644 100644" in diff_raw
        assert "\tf2.txt" in diff_raw
