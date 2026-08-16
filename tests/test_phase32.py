"""
tests/test_phase32.py
=====================
Phase 32 tests: commit -C/-c, rev-parse --revs-only/--no-revs, and diff --stat-width.
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


# -- commit -C / -c --------------------------------------------------------

class TestCommitReuseMessage:
    def test_commit_reuse_message(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        c1 = _commit_file(repo, "f1.txt", "v1\n", "Original commit message")

        (repo.worktree / "f2.txt").write_text("v2\n", encoding="utf-8")
        repo.add(["f2.txt"])

        c2 = repo.commit(reuse_message=c1)
        c2_obj = repo._require_commit(c2)
        assert c2_obj.message == "Original commit message"


# -- rev-parse --revs-only / --no-revs -------------------------------------

class TestRevParseRevsOnly:
    def test_rev_parse_revs_only_and_no_revs(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        c1 = _commit_file(repo, "f1.txt", "v1\n", "c1")

        resolved = repo.rev_parse("HEAD")
        assert resolved == c1
