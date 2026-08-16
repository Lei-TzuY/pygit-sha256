"""
tests/test_phase22.py
=====================
Phase 22 tests: commit --amend, ahead_behind calculation,
checkout -b <name> <start-point>, and stash apply --index.
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


# -- commit --amend ---------------------------------------------------------

class TestCommitAmend:
    def test_commit_amend_replaces_tip_and_updates_msg(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        c1 = _commit_file(repo, "f.txt", "v1\n", "Initial commit")
        c2 = _commit_file(repo, "f.txt", "v2\n", "Second commit")

        (repo.worktree / "f.txt").write_text("v2 amended\n", encoding="utf-8")
        repo.add(["f.txt"])

        c2_amended = repo.commit("Amended commit", amend=True)
        assert c2_amended != c2

        commits = repo.log()
        assert len(commits) == 2
        assert commits[0][0] == c2_amended
        assert commits[0][1].message == "Amended commit"
        assert commits[0][1].parents == [c1]


# -- ahead_behind calculation ----------------------------------------------

class TestAheadBehind:
    def test_ahead_behind_calculation(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        c1 = _commit_file(repo, "base.txt", "base\n", "base commit")

        repo.branch("feat")
        repo.checkout("feat")
        _commit_file(repo, "feat1.txt", "f1\n", "feat commit 1")
        _commit_file(repo, "feat2.txt", "f2\n", "feat commit 2")

        repo.checkout("main")
        _commit_file(repo, "main1.txt", "m1\n", "main commit 1")

        behind, ahead = repo.ahead_behind("main", "feat")
        # feat is 2 commits ahead of main's base divergence, and main has 1 commit feat doesn't have
        assert (behind, ahead) == (1, 2)


# -- checkout -b <name> <start-point> --------------------------------------

class TestCheckoutStartPoint:
    def test_checkout_b_with_start_point(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        c1 = _commit_file(repo, "f.txt", "v1\n", "c1")
        c2 = _commit_file(repo, "f.txt", "v2\n", "c2")

        repo.branch("from_c1", start_point=c1)
        assert repo.refs.get_branch("from_c1") == c1
        assert repo.refs.get_branch("main") == c2


# -- stash apply --index ----------------------------------------------------

class TestStashApplyIndex:
    def test_stash_apply_index_restores_staged_changes(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "base.txt", "base\n", "c1")

        # Staged change
        (repo.worktree / "staged.txt").write_text("staged\n", encoding="utf-8")
        repo.add(["staged.txt"])

        stash_sha = repo.stash_push("WIP")

        # Working tree clean after stash
        assert not (repo.worktree / "staged.txt").exists()

        repo.stash_apply(0, restore_index=True)

        assert (repo.worktree / "staged.txt").exists()
        status = repo.status()
        staged_paths = [p for _, p in status["staged"]]
        assert "staged.txt" in staged_paths
