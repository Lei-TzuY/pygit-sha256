"""
tests/test_phase19.py
=====================
Phase 19 tests: stash untracked files (-u), cherry-pick without committing (-n),
status short format (-s), and rev-list plumbing command.
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


# -- Stash Include Untracked (-u) ------------------------------------------

class TestStashIncludeUntracked:
    def test_stash_push_include_untracked(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "tracked.txt", "v1\n", "c1")

        # Create untracked file
        (repo.worktree / "untracked.txt").write_text("temp data\n", encoding="utf-8")
        assert (repo.worktree / "untracked.txt").exists()

        # Stash with include_untracked=True
        stash_sha = repo.stash_push(include_untracked=True)
        assert stash_sha
        assert not (repo.worktree / "untracked.txt").exists()


# -- Cherry-Pick No-Commit (-n) --------------------------------------------

class TestCherryPickNoCommit:
    def test_cherry_pick_no_commit(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        c1 = _commit_file(repo, "file.txt", "v1\n", "c1")

        repo.branch("feature")
        repo.checkout("feature")
        c2 = _commit_file(repo, "file.txt", "v2\n", "c2 feature")

        repo.checkout("main")
        res = repo.cherry_pick(c2, no_commit=True)
        assert res["status"] == "applied"
        assert res["sha"] is None

        # Check worktree updated but HEAD commit is still c1
        assert (repo.worktree / "file.txt").read_text() == "v2\n"
        assert repo.refs.resolve_head() == c1


# -- Status Short Format (-s) ----------------------------------------------

class TestStatusShort:
    def test_format_short_status(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "staged.txt", "v1\n", "c1")

        (repo.worktree / "staged.txt").write_text("v2\n", encoding="utf-8")
        repo.add(["staged.txt"])

        (repo.worktree / "unstaged.txt").write_text("dirty\n", encoding="utf-8")
        (repo.worktree / "untracked.txt").write_text("new\n", encoding="utf-8")

        lines = repo.format_short_status()
        assert any(l.startswith("M ") and "staged.txt" in l for l in lines)
        assert any(l.startswith("?? ") and "untracked.txt" in l for l in lines)


# -- Rev-List Plumbing Command ---------------------------------------------

class TestRevList:
    def test_rev_list_log_order(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        c1 = _commit_file(repo, "f.txt", "v1\n", "c1")
        c2 = _commit_file(repo, "f.txt", "v2\n", "c2")

        commits = repo.log(start="HEAD")
        assert len(commits) == 2
        assert commits[0][0] == c2
        assert commits[1][0] == c1
