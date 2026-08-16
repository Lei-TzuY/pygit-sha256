"""
tests/test_phase26.py
=====================
Phase 26 tests: rev-parse --symbolic-full-name, log --first-parent, diff -w/-b, and reset -p.
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


# -- rev-parse --symbolic-full-name ----------------------------------------

class TestRevParseSymbolicFullName:
    def test_rev_parse_symbolic_full_name(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        c1 = _commit_file(repo, "f.txt", "v1\n", "c1")
        repo.tag("v1.0")

        ref_head = repo.rev_parse("HEAD", symbolic_full_name=True)
        assert ref_head == "refs/heads/main"

        ref_tag = repo.rev_parse("v1.0", symbolic_full_name=True)
        assert ref_tag == "refs/tags/v1.0"


# -- log --first-parent ----------------------------------------------------

class TestLogFirstParent:
    def test_log_first_parent(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        c1 = _commit_file(repo, "f.txt", "v1\n", "c1")

        repo.branch("b1")
        repo.checkout("b1")
        c2 = _commit_file(repo, "f1.txt", "v1\n", "c2")

        repo.checkout("main")
        c3 = _commit_file(repo, "f2.txt", "v2\n", "c3")

        repo.merge("b1")
        merge_sha = repo.refs.resolve_head()

        # log --first-parent follows main line (merge -> c3 -> c1), omitting c2
        commits = repo.log(first_parent=True)
        shas = [s for s, _ in commits]

        assert merge_sha in shas
        assert c3 in shas
        assert c1 in shas
        assert c2 not in shas


# -- diff -w / -b ----------------------------------------------------------

class TestDiffWhitespace:
    def test_diff_ignore_whitespace(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "f.txt", "hello world\n", "c1")

        # Add spaces in worktree
        (repo.worktree / "f.txt").write_bytes(b"hello   world\n")

        # Standard diff shows modification
        diff_normal = repo.diff()
        assert "hello   world" in diff_normal

        # diff -w / diff -b ignores space change
        diff_w = repo.diff(ignore_all_space=True)
        assert diff_w == ""

        diff_b = repo.diff(ignore_space_change=True)
        assert diff_b == ""


# -- reset -p --------------------------------------------------------------

class TestResetPatch:
    def test_reset_patch_unstages_files(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "f.txt", "v1\n", "c1")

        (repo.worktree / "f.txt").write_bytes(b"v2\n")
        repo.add(["f.txt"])
        assert repo.status()["staged"]

        # reset -p unstages f.txt
        res = repo.reset_patch(paths=["f.txt"])
        assert res == 1
        assert not repo.status()["staged"]
