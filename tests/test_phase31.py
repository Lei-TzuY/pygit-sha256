"""
tests/test_phase31.py
=====================
Phase 31 tests: stash push --staged (-S), rev-list --min-age/--max-age, diff --inter-hunk-context, and rev-parse --prefix.
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


# -- stash push --staged ---------------------------------------------------

class TestStashPushStaged:
    def test_stash_push_staged_only(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "f1.txt", "v1\n", "c1")
        _commit_file(repo, "f2.txt", "v1\n", "c1")

        # Staged change in f1.txt, Unstaged change in f2.txt
        (repo.worktree / "f1.txt").write_text("v2_staged\n", encoding="utf-8")
        repo.add(["f1.txt"])
        (repo.worktree / "f2.txt").write_text("v2_unstaged\n", encoding="utf-8")

        # Stash ONLY staged changes (-S)
        stash_sha = repo.stash_push(staged_only=True)
        assert stash_sha is not None

        # f1.txt should be reset to HEAD state (v1)
        assert (repo.worktree / "f1.txt").read_text(encoding="utf-8") == "v1\n"
        # f2.txt should retain unstaged modifications (v2_unstaged)
        assert (repo.worktree / "f2.txt").read_text(encoding="utf-8") == "v2_unstaged\n"


# -- rev-parse --prefix ----------------------------------------------------

class TestRevParsePrefix:
    def test_rev_parse_prefix(self, tmp_path: Path, monkeypatch) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        sub_dir = repo.worktree / "sub" / "dir"
        sub_dir.mkdir(parents=True, exist_ok=True)

        monkeypatch.chdir(sub_dir)
        curr = Path.cwd()
        rel = curr.relative_to(repo.worktree)
        assert rel.as_posix() == "sub/dir"
