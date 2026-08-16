"""
tests/test_phase39.py
=====================
Phase 39 tests: rev-parse --abbrev-ref and commit --quiet / -q.
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


# -- rev-parse --abbrev-ref ------------------------------------------------

class TestRevParseAbbrevRef:
    def test_rev_parse_abbrev_ref(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "f1.txt", "v1\n", "c1")
        # current branch is main
        assert repo.refs.get_head() == "ref: refs/heads/main"
        assert repo.refs.current_branch() == "main"
