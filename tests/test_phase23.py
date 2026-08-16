"""
tests/test_phase23.py
=====================
Phase 23 tests: count-objects -v, checkout --detach, rev-list --topo-order, and status --porcelain.
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


# -- count-objects -v ------------------------------------------------------

class TestCountObjects:
    def test_count_objects_verbose(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "f.txt", "v1\n", "c1")

        info = repo.count_objects()
        assert "count" in info
        assert "size_kb" in info
        assert "in_pack" in info
        assert "packs" in info
        assert "size_pack_kb" in info
        assert info["count"] > 0


# -- checkout --detach -----------------------------------------------------

class TestCheckoutDetach:
    def test_checkout_detach_sets_detached_head(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        c1 = _commit_file(repo, "f.txt", "v1\n", "c1")
        c2 = _commit_file(repo, "f.txt", "v2\n", "c2")

        repo.checkout(c1)
        assert repo.refs.current_branch() is None
        assert repo.refs.resolve_head() == c1


# -- rev-list --topo-order -------------------------------------------------

class TestRevListTopoOrder:
    def test_rev_list_topo_order(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        c1 = _commit_file(repo, "f.txt", "v1\n", "c1")

        repo.branch("b1")
        repo.checkout("b1")
        c2 = _commit_file(repo, "f1.txt", "v1\n", "c2")

        repo.checkout("main")
        c3 = _commit_file(repo, "f2.txt", "v2\n", "c3")

        repo.merge("b1")
        merge_commit = repo.refs.resolve_head()

        topo_commits = repo.log(topo_order=True)
        shas = [sha for sha, _ in topo_commits]

        # In topological order, children appear before their parents
        assert shas[0] == merge_commit
        assert shas.index(merge_commit) < shas.index(c2)
        assert shas.index(merge_commit) < shas.index(c3)
        assert shas.index(c2) < shas.index(c1)
        assert shas.index(c3) < shas.index(c1)


# -- status --porcelain ----------------------------------------------------

class TestStatusPorcelain:
    def test_status_porcelain_output(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "tracked.txt", "v1\n", "c1")

        (repo.worktree / "tracked.txt").write_text("v2\n", encoding="utf-8")
        (repo.worktree / "untracked.txt").write_text("new\n", encoding="utf-8")

        status = repo.status(ignored=True)
        assert len(status["unstaged"]) > 0
        assert "untracked.txt" in status["untracked"]
