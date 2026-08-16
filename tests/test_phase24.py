"""
tests/test_phase24.py
=====================
Phase 24 tests: clone --single-branch, log --merges/--no-merges, diff --name-status/--name-only, and describe --tags/--always.
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


# -- log --merges / --no-merges --------------------------------------------

class TestLogMerges:
    def test_log_merges_and_no_merges(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        c1 = _commit_file(repo, "f.txt", "v1\n", "c1")

        repo.branch("b1")
        repo.checkout("b1")
        c2 = _commit_file(repo, "f1.txt", "v1\n", "c2")

        repo.checkout("main")
        c3 = _commit_file(repo, "f2.txt", "v2\n", "c3")

        repo.merge("b1")
        merge_sha = repo.refs.resolve_head()

        # log --merges
        merges = repo.log(merges_only=True)
        assert len(merges) == 1
        assert merges[0][0] == merge_sha

        # log --no-merges
        no_merges = repo.log(merges_only=False)
        shas = [s for s, _ in no_merges]
        assert merge_sha not in shas
        assert c1 in shas
        assert c3 in shas


# -- diff --name-status / --name-only ---------------------------------------

class TestDiffNameStatus:
    def test_diff_name_status_and_name_only(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "f1.txt", "v1\n", "c1")

        (repo.worktree / "f1.txt").write_text("v2\n", encoding="utf-8")

        diff_ns = repo.diff(name_status=True)
        assert "M\tf1.txt" in diff_ns

        diff_no = repo.diff(name_only=True)
        assert "f1.txt" in diff_no
        assert "M\t" not in diff_no


# -- describe --tags / --always ---------------------------------------------

class TestDescribeTagsAlways:
    def test_describe_tags_and_always(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        c1 = _commit_file(repo, "f.txt", "v1\n", "c1")

        # Create lightweight tag
        repo.tag("lw-tag")

        # With default tags=True: lightweight tag is matched
        desc_default = repo.describe()
        assert desc_default == "lw-tag"

        # With tags=False: lightweight tag is ignored, fallback to g<sha>
        desc_no_tags = repo.describe(tags=False)
        assert desc_no_tags == f"g{c1[:7]}"

        # Test --always without tags
        desc_always = repo.describe(tags=False, always=True)
        assert desc_always == c1[:7]
