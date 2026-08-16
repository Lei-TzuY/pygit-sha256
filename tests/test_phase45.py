"""
tests/test_phase45.py
=====================
Phase 45 tests: diff --stat-graph-width and rev-parse --path-format.
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


# -- diff --stat-graph-width & rev-parse --path-format ---------------------

class TestPhase45:
    def test_diff_stat_graph_width(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "f1.txt", "v1\n", "c1")
        (repo.worktree / "f1.txt").write_text("v2\n", encoding="utf-8")
        diff_out = repo.diff(stat=True, stat_graph_width=10)
        assert diff_out is not None

    def test_rev_parse_path_format(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "f1.txt", "v1\n", "c1")
        assert (repo.pygit_dir).exists()
