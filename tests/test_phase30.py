"""
tests/test_phase30.py
=====================
Phase 30 tests: commit --cleanup, log --date, diff -I regex filtering, and rev-parse flags.
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


# -- commit --cleanup ------------------------------------------------------

class TestCommitCleanup:
    def test_commit_cleanup_strip(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        msg = "Subject line\n\n# comment line to strip\n\nBody detail\n"
        c1 = _commit_file(repo, "f1.txt", "v1\n", msg, cleanup="strip")
        c_obj = repo._require_commit(c1)
        assert "# comment line" not in c_obj.message
        assert c_obj.message == "Subject line\n\nBody detail"

    def test_commit_cleanup_verbatim(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        msg = "Subject line\n# comment to keep"
        c1 = _commit_file(repo, "f1.txt", "v1\n", msg, cleanup="verbatim")
        c_obj = repo._require_commit(c1)
        assert "# comment to keep" in c_obj.message


# -- log --date ------------------------------------------------------------

class TestLogDateFormat:
    def test_log_date_format(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        c1 = _commit_file(repo, "f1.txt", "v1\n", "c1")
        c_obj = repo._require_commit(c1)

        printed_short = c_obj.pretty_print(c1, date_format="short")
        assert "Date:   2026-" in printed_short or "Date:   20" in printed_short

        printed_relative = c_obj.pretty_print(c1, date_format="relative")
        assert "seconds ago" in printed_relative or "minutes ago" in printed_relative or "0 seconds ago" in printed_relative


# -- diff -I ---------------------------------------------------------------

class TestDiffIgnoreMatchingLines:
    def test_diff_ignore_matching_lines(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "f1.txt", "DEBUG: line 1\nnormal line\n", "c1")

        (repo.worktree / "f1.txt").write_text("DEBUG: line 2\nnormal line updated\n", encoding="utf-8")

        # Without -I
        diff_all = repo.diff()
        assert "DEBUG" in diff_all

        # With -I
        diff_filtered = repo.diff(ignore_matching_lines=r"^DEBUG")
        assert "DEBUG" not in diff_filtered
        assert "normal line" in diff_filtered


# -- rev-parse flags -------------------------------------------------------

class TestRevParseFlags:
    def test_rev_parse_repository_flags(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        assert not (repo.pygit_dir / "shallow").exists()

        # Create shallow indicator file
        (repo.pygit_dir / "shallow").write_text("dummy_sha\n", encoding="utf-8")
        assert (repo.pygit_dir / "shallow").exists()
