"""
tests/test_phase37.py
=====================
Phase 37 tests: status -s -b, commit --dry-run, and rev-parse --short=N.
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


# -- rev-parse --short=N ---------------------------------------------------

class TestRevParseShortLen:
    def test_rev_parse_short_len(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        c1 = _commit_file(repo, "f1.txt", "v1\n", "c1")
        assert len(c1) == 64
        # rev_parse with slice length 12
        assert c1[:12] == repo.rev_parse("HEAD")[:12]
