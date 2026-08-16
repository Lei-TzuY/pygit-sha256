"""
tests/test_phase25.py
=====================
Phase 25 tests: stash create/store, commit --only/--include, log -L line trace, and branch --contains/--no-contains.
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


# -- stash create / store --------------------------------------------------

class TestStashCreateStore:
    def test_stash_create_and_store(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "f.txt", "v1\n", "c1")

        (repo.worktree / "f.txt").write_text("dirty\n", encoding="utf-8")

        # Create stash commit without changing worktree or refs/stash
        stash_sha = repo.stash_create(message="custom stash")
        assert stash_sha is not None
        assert repo.refs.get_stash() is None
        assert (repo.worktree / "f.txt").read_text(encoding="utf-8") == "dirty\n"

        # Store created stash SHA into refs/stash
        stored = repo.stash_store(stash_sha, message="custom stash")
        assert stored == stash_sha
        assert repo.refs.get_stash() == stash_sha


# -- commit --only / --include ---------------------------------------------

class TestCommitOnlyInclude:
    def test_commit_only_paths(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "f1.txt", "v1\n", "c1")
        _commit_file(repo, "f2.txt", "v1\n", "c2")

        (repo.worktree / "f1.txt").write_text("v2\n", encoding="utf-8")
        (repo.worktree / "f2.txt").write_text("v2\n", encoding="utf-8")
        repo.add(["f1.txt", "f2.txt"])

        # Commit only f1.txt
        c3 = repo.commit("commit f1 only", only_paths=["f1.txt"])

        # f1.txt is committed; f2.txt remains staged
        c3_obj = repo.store.read(c3)
        c3_tree = repo._commit_tree_entries(c3)
        assert c3_tree["f1.txt"][0] != c3_tree["f2.txt"][0]
        assert "f2.txt" in repo.index

    def test_commit_include_paths(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "f1.txt", "v1\n", "c1")
        _commit_file(repo, "f2.txt", "v1\n", "c2")

        (repo.worktree / "f1.txt").write_bytes(b"v2\n")
        (repo.worktree / "f2.txt").write_bytes(b"v2\n")
        repo.add(["f1.txt"])

        # Commit with --include f2.txt
        c3 = repo.commit("commit with include", include_paths=["f2.txt"])
        c3_tree = repo._commit_tree_entries(c3)
        assert repo._blob_bytes(c3_tree["f1.txt"][0]).strip() == b"v2"
        assert repo._blob_bytes(c3_tree["f2.txt"][0]).strip() == b"v2"


# -- log -L line trace -----------------------------------------------------

class TestLogLineRange:
    def test_log_line_range_trace(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        c1 = _commit_file(repo, "f.txt", "line1\nline2\nline3\n", "c1")
        c2 = _commit_file(repo, "f.txt", "line1\nline2_modified\nline3\n", "c2")
        c3 = _commit_file(repo, "other.txt", "hello\n", "c3")

        # Log changes for lines 2..2 in f.txt
        commits = repo.log(line_range=(2, 2, "f.txt"))
        shas = [sha for sha, _ in commits]

        assert c2 in shas
        assert c1 in shas
        assert c3 not in shas


# -- branch --contains / --no-contains -------------------------------------

class TestBranchContains:
    def test_branch_contains_and_no_contains(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        c1 = _commit_file(repo, "f.txt", "v1\n", "c1")

        repo.branch("b1")
        repo.checkout("b1")
        c2 = _commit_file(repo, "f.txt", "v2\n", "c2")

        repo.checkout("main")

        contains_b1 = repo.branch(contains=c2)
        assert "b1" in contains_b1
        assert "main" not in contains_b1

        no_contains_b1 = repo.branch(no_contains=c2)
        assert "main" in no_contains_b1
        assert "b1" not in no_contains_b1
