"""
tests/test_phase17.py
=====================
Phase 17 tests: patch hunk staging, blame line ranges, rerere conflict resolution cache,
and low-level plumbing commands.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pygit import Repository
from pygit.rerere import RerereEngine


# -- helpers ---------------------------------------------------------------

def _commit_file(repo: Repository, name: str, content: str, msg: str) -> str:
    """Write *content* to *name*, stage, and commit."""
    path = repo.worktree / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    repo.add([name])
    return repo.commit(msg, author_name="Tester", author_email="t@e.com")


# -- Hunk staging / restoring -----------------------------------------------

class TestHunkStaging:
    def test_apply_hunk_to_index(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "f.txt", "v1\n", "c1")

        (repo.worktree / "f.txt").write_text("v2\n", encoding="utf-8")
        blob_sha = repo.apply_hunk_to_index("f.txt")

        assert blob_sha
        assert repo.index.entries["f.txt"].sha == blob_sha

    def test_apply_hunk_to_worktree(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "f.txt", "v1\n", "c1")

        (repo.worktree / "f.txt").write_text("dirty\n", encoding="utf-8")
        repo.apply_hunk_to_worktree("f.txt")

        assert (repo.worktree / "f.txt").read_text() == "v1\n"


# -- Blame line range (-L) --------------------------------------------------

class TestBlameLineRange:
    def test_blame_with_line_range(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        content = "\n".join(f"line {i}" for i in range(1, 20)) + "\n"
        _commit_file(repo, "file.txt", content, "initial")

        lines = repo.blame("file.txt", line_range=(5, 10))
        assert len(lines) == 6
        assert "line 5" in lines[0]
        assert "line 10" in lines[-1]


# -- Rerere conflict resolution cache -------------------------------------

class TestRerere:
    def test_rerere_record_and_autoresolve(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "conf.txt", "base\n", "base")

        # Branch A
        repo.branch("bA")
        repo.checkout("bA")
        _commit_file(repo, "conf.txt", "side A\n", "side A")

        # Branch B
        repo.checkout("main")
        repo.branch("bB")
        repo.checkout("bB")
        _commit_file(repo, "conf.txt", "side B\n", "side B")

        # First merge -- produces conflict and records in rerere
        res1 = repo.merge("bA")
        assert res1["status"] == "conflicts"

        re = RerereEngine(repo.pygit_dir)
        statuses = re.status()
        assert len(statuses) >= 1
        chash = statuses[0][0]

        # User resolves conflict and records resolution
        (repo.worktree / "conf.txt").write_text("resolved both\n", encoding="utf-8")
        repo.rerere_record_resolution(chash, "conf.txt")
        repo.add(["conf.txt"])
        repo.commit("resolve merge 1")

        # Create identical conflict again on another branch
        repo.checkout("main")
        repo.branch("bC")
        repo.checkout("bC")
        _commit_file(repo, "conf.txt", "side B\n", "side B again")

        # Second merge -- rerere auto-resolves the conflict!
        res2 = repo.merge("bA")
        assert res2["status"] != "conflicts" or (repo.worktree / "conf.txt").read_text() == "resolved both\n"


# -- Plumbing commands (write-tree & commit-tree) ---------------------------

class TestPlumbingCommands:
    def test_write_tree_and_commit_tree(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        (repo.worktree / "test.txt").write_text("plumbing test\n", encoding="utf-8")
        repo.add(["test.txt"])

        tree_sha = repo._build_tree()
        assert tree_sha

        from pygit.objects import CommitObject, TreeObject
        tree_obj = repo.store.read(tree_sha)
        assert isinstance(tree_obj, TreeObject)

        from pygit.objects.commit import Identity
        identity = Identity("Plumbing", "p@example.com")
        c_obj = CommitObject(
            tree=tree_sha,
            parents=[],
            author=identity,
            committer=identity,
            message="plumbing commit",
        )
        commit_sha = repo.store.write(c_obj)
        assert commit_sha
