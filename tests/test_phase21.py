"""
tests/test_phase21.py
=====================
Phase 21 tests: pygit mv, pygit ls-tree, pygit rev-parse flags, and pygit status --ignored.
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


# -- pygit mv ---------------------------------------------------------------

class TestPygitMv:
    def test_mv_single_file(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "old.txt", "hello\n", "initial")

        repo.mv("old.txt", "new.txt")

        assert not (repo.worktree / "old.txt").exists()
        assert (repo.worktree / "new.txt").read_text(encoding="utf-8") == "hello\n"

        status = repo.status()
        staged_paths = [p for _, p in status["staged"]]
        assert "old.txt" in staged_paths
        assert "new.txt" in staged_paths

    def test_mv_directory(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "src/a.txt", "a\n", "c1")
        _commit_file(repo, "src/b.txt", "b\n", "c2")

        repo.mv("src", "dst")

        assert not (repo.worktree / "src").exists()
        assert (repo.worktree / "dst" / "a.txt").exists()
        assert (repo.worktree / "dst" / "b.txt").exists()


# -- pygit ls-tree ----------------------------------------------------------

class TestLsTree:
    def test_ls_tree_flat_and_recursive(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "root.txt", "r\n", "c1")
        _commit_file(repo, "dir/child.txt", "c\n", "c2")

        # Flat listing
        lines = repo.ls_tree("HEAD", recursive=False)
        paths = [line.split("\t")[-1] for line in lines]
        assert "root.txt" in paths
        assert "dir" in paths

        # Recursive listing
        lines_rec = repo.ls_tree("HEAD", recursive=True)
        paths_rec = [line.split("\t")[-1] for line in lines_rec]
        assert "root.txt" in paths_rec
        assert "dir/child.txt" in paths_rec

        # Name only
        names_only = repo.ls_tree("HEAD", recursive=True, name_only=True)
        assert "root.txt" in names_only
        assert "dir/child.txt" in names_only


# -- pygit rev-parse flags --------------------------------------------------

class TestRevParseFlags:
    def test_rev_parse_flags(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        sha = _commit_file(repo, "f.txt", "v1\n", "c1")

        parsed_full = repo.rev_parse("HEAD")
        assert parsed_full == sha

        parsed_short = repo.rev_parse("HEAD")[:12]
        assert len(parsed_short) == 12
        assert sha.startswith(parsed_short)


# -- status --ignored -------------------------------------------------------

class TestStatusIgnored:
    def test_status_ignored(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        (repo.worktree / ".gitignore").write_text("*.log\n", encoding="utf-8")
        repo.add([".gitignore"])
        repo.commit("add gitignore")

        (repo.worktree / "debug.log").write_text("log content\n", encoding="utf-8")

        status_normal = repo.status(ignored=False)
        assert "debug.log" not in status_normal["untracked"]
        assert "ignored" not in status_normal

        status_ignored = repo.status(ignored=True)
        assert "debug.log" in status_ignored["ignored"]
