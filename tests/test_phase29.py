"""
tests/test_phase29.py
=====================
Phase 29 tests: branch --merged/--no-merged, commit --allow-empty, log --min-parents/--max-parents, and show-branch.
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


# -- branch --merged / --no-merged -----------------------------------------

class TestBranchMerged:
    def test_branch_merged_and_no_merged(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        c1 = _commit_file(repo, "f1.txt", "v1\n", "c1")

        repo.branch("b_merged")
        repo.branch("b_unmerged")
        repo.checkout("b_unmerged")
        _commit_file(repo, "f2.txt", "v2\n", "c2")
        repo.checkout("main")

        merged = repo.branch(merged="HEAD")
        assert "b_merged" in merged
        assert "b_unmerged" not in merged

        no_merged = repo.branch(no_merged="HEAD")
        assert "b_unmerged" in no_merged
        assert "b_merged" not in no_merged


# -- commit --allow-empty --------------------------------------------------

class TestCommitAllowEmpty:
    def test_commit_allow_empty(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        c1 = _commit_file(repo, "f1.txt", "v1\n", "c1")

        # Committing again with clean tree raises error unless allow_empty is True
        with pytest.raises(RuntimeError):
            repo.commit("empty commit without flag")

        c2 = repo.commit("empty commit with flag", allow_empty=True)
        assert c2 != c1
        assert repo._require_commit(c2).tree == repo._require_commit(c1).tree


# -- log --min-parents / --max-parents -------------------------------------

class TestLogMinMaxParents:
    def test_log_min_max_parents(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        c1 = _commit_file(repo, "f1.txt", "v1\n", "c1")

        repo.branch("b1")
        repo.checkout("b1")
        c2 = _commit_file(repo, "f2.txt", "v2\n", "c2")

        repo.checkout("main")
        c3 = _commit_file(repo, "f3.txt", "v3\n", "c3")

        repo.merge("b1")
        merge_sha = repo.refs.resolve_head()

        # min-parents=2 -> only merge commit
        merges = repo.log(min_parents=2)
        shas = [s for s, _ in merges]
        assert merge_sha in shas
        assert c2 not in shas

        # max-parents=1 -> non-merges only
        non_merges = repo.log(max_parents=1)
        nm_shas = [s for s, _ in non_merges]
        assert merge_sha not in nm_shas
        assert c3 in nm_shas


# -- show-branch -----------------------------------------------------------

class TestShowBranch:
    def test_show_branch_output(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        c1 = _commit_file(repo, "f1.txt", "v1\n", "c1")
        repo.branch("feature")

        sb = repo.show_branch()
        assert "[main]" in sb
        assert "[feature]" in sb
