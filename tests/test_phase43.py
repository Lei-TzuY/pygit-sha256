"""
tests/test_phase43.py
=====================
Phase 43 tests: rev-parse --is-shallow-repository and commit --amend --no-edit.
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


# -- rev-parse --is-shallow-repository & commit --amend --no-edit ----------

class TestPhase43:
    def test_rev_parse_is_shallow_repository(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "f1.txt", "v1\n", "c1")
        assert not (repo.pygit_dir / "shallow").exists()

    def test_commit_amend_no_edit(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        c1 = _commit_file(repo, "f1.txt", "v1\n", "Original Message")
        (repo.worktree / "f1.txt").write_text("v2\n", encoding="utf-8")
        repo.add(["f1.txt"])
        c2 = repo.commit("Original Message", amend=True)
        c_obj = repo.store.read(c2)
        assert c_obj.message == "Original Message"
