"""
tests/test_phase28.py
=====================
Phase 28 tests: stash clear, status -s header with upstream info, and rev-parse --branches/--tags/--remotes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pygit import Repository


# -- helpers ---------------------------------------------------------------

def _commit_file(repo: Repository, name: str, content: str, msg: str) -> str:
    """Write *content* to *name*, stage, and commit."""
    path = repo.worktree / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    repo.add([name])
    return repo.commit(msg, author_name="Tester", author_email="t@e.com")


# -- stash clear -----------------------------------------------------------

class TestStashClear:
    def test_stash_clear(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "f.txt", "v1\n", "c1")

        (repo.worktree / "f.txt").write_text("v2\n", encoding="utf-8")
        repo.stash_push("stash 1")

        (repo.worktree / "f.txt").write_text("v3\n", encoding="utf-8")
        repo.stash_push("stash 2")

        assert len(repo.stash_list()) == 2

        # Clear all stashes
        repo.stash_clear()
        assert len(repo.stash_list()) == 0
        assert repo.refs.get_stash() is None


# -- rev-parse namespaces --------------------------------------------------

class TestRevParseNamespaces:
    def test_rev_parse_namespaces(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        c1 = _commit_file(repo, "f.txt", "v1\n", "c1")
        repo.tag("v1.0")

        repo.branch("feat")
        c2 = _commit_file(repo, "f2.txt", "v2\n", "c2")

        branch_shas = repo.rev_parse_namespaces(branches=True)
        assert c1 in branch_shas or c2 in branch_shas

        tag_shas = repo.rev_parse_namespaces(tags=True)
        assert c1 in tag_shas


# -- status upstream info --------------------------------------------------

class TestStatusUpstreamInfo:
    def test_status_upstream_info(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "f.txt", "v1\n", "c1")

        st = repo.status()
        assert st["branch"] == "main"
        assert "upstream" in st
