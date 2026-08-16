"""
tests/test_phase18.py
=====================
Phase 18 tests: maintenance pipeline, check-ignore diagnostics,
ref plumbing commands (update-ref / symbolic-ref), and filter-branch history rewrite.
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


# -- Maintenance Pipeline ---------------------------------------------------

class TestMaintenance:
    def test_maintenance_run(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "a.txt", "v1", "c1")
        _commit_file(repo, "b.txt", "v2", "c2")

        info = repo.maintenance()
        assert info["pack"] is not None
        assert Path(info["commit_graph"]).exists()


# -- Check-Ignore Diagnostics ----------------------------------------------

class TestCheckIgnore:
    def test_check_ignore_diagnostics(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        (repo.worktree / ".gitignore").write_text("*.log\ntemp/\n", encoding="utf-8")

        matches = repo.check_ignore(["app.log", "src/main.py", "temp/cache.bin"])
        ignored_paths = [m[0] for m in matches]
        assert "app.log" in ignored_paths
        assert "temp/cache.bin" in ignored_paths
        assert "src/main.py" not in ignored_paths


# -- Ref Plumbing Commands -------------------------------------------------

class TestRefPlumbing:
    def test_symbolic_ref_and_update_ref(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        c1 = _commit_file(repo, "a.txt", "v1", "c1")
        c2 = _commit_file(repo, "a.txt", "v2", "c2")

        # Symbolic-ref
        repo.refs.set_head_symbolic("feature", message="switch feature")
        assert repo.refs.current_branch() == "feature"

        # Update-ref
        repo.refs.set_branch("feature", c1, message="update-ref c1")
        assert repo.refs.get_branch("feature") == c1


# -- Filter Branch ---------------------------------------------------------

class TestFilterBranch:
    def test_filter_branch_rewrites_history(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "keep/a.txt", "keep me", "c1")
        _commit_file(repo, "drop/b.txt", "drop me", "c2")

        new_tip = repo.filter_branch("keep/", branch_name="main")
        assert new_tip

        # Verify only keep/ files exist in new commit tree
        c_obj = repo.store.read(new_tip)
        tree_flat = {}
        repo._flatten_tree(c_obj.tree, "", tree_flat)

        assert "keep/a.txt" in tree_flat
        assert "drop/b.txt" not in tree_flat
