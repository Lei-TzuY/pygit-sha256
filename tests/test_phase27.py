"""
tests/test_phase27.py
=====================
Phase 27 tests: stash push --keep-index, rev-list --left-right, and 3-way diff3 conflict rendering.
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


# -- stash push --keep-index -----------------------------------------------

class TestStashKeepIndex:
    def test_stash_push_keep_index(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "f1.txt", "v1\n", "c1")
        _commit_file(repo, "f2.txt", "v1\n", "c2")

        (repo.worktree / "f1.txt").write_text("v2_staged\n", encoding="utf-8")
        repo.add(["f1.txt"])
        (repo.worktree / "f2.txt").write_text("v2_unstaged\n", encoding="utf-8")

        # Stash with keep_index
        repo.stash_push("stash with keep-index", keep_index=True)

        # f1.txt should remain staged in index
        staged = repo.status()["staged"]
        assert ("modified", "f1.txt") in staged

        # f2.txt should be reverted to HEAD state
        assert (repo.worktree / "f2.txt").read_text(encoding="utf-8") == "v1\n"


# -- rev-list --left-right -------------------------------------------------

class TestRevListLeftRight:
    def test_rev_list_left_right(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        c1 = _commit_file(repo, "f.txt", "base\n", "c1")

        repo.branch("feat")
        repo.checkout("feat")
        c_feat = _commit_file(repo, "f_feat.txt", "feat\n", "c_feat")

        repo.checkout("main")
        c_main = _commit_file(repo, "f_main.txt", "main\n", "c_main")

        # rev-list main...feat
        left_commits = {sha for sha, _ in repo.log(start="main")}
        right_commits = {sha for sha, _ in repo.log(start="feat")}

        assert c_main in (left_commits - right_commits)
        assert c_feat in (right_commits - left_commits)


# -- conflict style diff3 --------------------------------------------------

class TestConflictStyleDiff3:
    def test_three_way_merge_diff3(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        base = b"line1\nline2\nline3\n"
        ours = b"line1\nline2_ours\nline3\n"
        theirs = b"line1\nline2_theirs\nline3\n"

        merged, has_conflict = repo._merge_lines_three_way(base, ours, theirs, "target", conflict_style="diff3")
        assert has_conflict
        merged_str = merged.decode("utf-8")

        assert "<<<<<<< HEAD" in merged_str
        assert "||||||| base" in merged_str
        assert "=======" in merged_str
        assert ">>>>>>> target" in merged_str
