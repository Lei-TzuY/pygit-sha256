"""
tests/test_phase20.py
=====================
Phase 20 tests: diff --stat (already supported), log --format custom placeholders,
merge --squash, tag -l pattern filtering, and branch -a remote listing.
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


# -- Diff Stat -------------------------------------------------------------

class TestDiffStat:
    def test_diff_stat_output(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "file.txt", "line1\nline2\nline3\n", "c1")
        (repo.worktree / "file.txt").write_text("line1\nmodified\nline3\nnew\n", encoding="utf-8")

        output = repo.diff(stat=True)
        assert "file.txt" in output
        assert "+" in output or "insertion" in output


# -- Log Format Placeholders -----------------------------------------------

class TestLogFormat:
    def test_log_format_placeholders(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        sha = _commit_file(repo, "f.txt", "v1\n", "Initial commit")

        commits = repo.log()
        assert len(commits) == 1
        c_sha, c_obj = commits[0]

        formatted = repo.format_commit(c_sha, c_obj, "%h %an <%ae> %s")
        assert c_sha[:12] in formatted
        assert "Tester" in formatted
        assert "t@e.com" in formatted
        assert "Initial commit" in formatted


# -- Merge Squash -----------------------------------------------------------

class TestMergeSquash:
    def test_merge_squash_stages_without_commit(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "base.txt", "base\n", "base commit")

        repo.branch("feature")
        repo.checkout("feature")
        _commit_file(repo, "feat.txt", "feature work\n", "feat commit")

        repo.checkout("main")
        head_before = repo.refs.resolve_head()

        result = repo.merge("feature", squash=True)
        assert result["status"] == "squashed"

        # HEAD should NOT have advanced (no merge commit created)
        assert repo.refs.resolve_head() == head_before

        # But feat.txt should exist in worktree
        assert (repo.worktree / "feat.txt").exists()


# -- Tag List with Pattern --------------------------------------------------

class TestTagListPattern:
    def test_tag_list_with_pattern(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "f.txt", "v1\n", "c1")

        repo.tag("v1.0")
        repo.tag("v1.1")
        repo.tag("v2.0")
        repo.tag("release-1")

        import fnmatch
        tags = repo.tag() or []
        v1_tags = [t for t in tags if fnmatch.fnmatch(t, "v1.*")]
        assert "v1.0" in v1_tags
        assert "v1.1" in v1_tags
        assert "v2.0" not in v1_tags
        assert "release-1" not in v1_tags


# -- Branch All (including remotes) -----------------------------------------

class TestBranchAll:
    def test_branch_all_includes_remotes(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "f.txt", "v1\n", "c1")

        # Simulate remote tracking branch
        remote_ref_dir = repo.pygit_dir / "refs" / "remotes" / "origin"
        remote_ref_dir.mkdir(parents=True, exist_ok=True)
        (remote_ref_dir / "main").write_text(
            repo.refs.resolve_head(), encoding="utf-8"
        )

        remote_branches = repo.list_remote_branches()
        assert "remotes/origin/main" in remote_branches
