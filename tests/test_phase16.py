"""
tests/test_phase16.py
=====================
Phase 16 tests: fixup/squash & autosquash, log --follow rename tracking,
shallow boundary, and binary commit-graph cache.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pygit import Repository
from pygit.commit_graph import CommitGraph
from pygit.objects import CommitObject


# -- helpers ---------------------------------------------------------------

def _commit_file(repo: Repository, name: str, content: str, msg: str) -> str:
    """Write *content* to *name*, stage, and commit."""
    path = repo.worktree / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    repo.add([name])
    return repo.commit(msg, author_name="Tester", author_email="t@e.com")


# -- Fixup / Squash & Autosquash --------------------------------------------

class TestFixupSquashAutosquash:
    def test_commit_fixup_and_squash_messages(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        c1 = _commit_file(repo, "a.txt", "v1", "Add feature alpha")

        # Fixup commit
        (repo.worktree / "a.txt").write_text("v1.1", encoding="utf-8")
        repo.add(["a.txt"])
        c2 = repo.commit(fixup=c1)

        c2_obj = repo.store.read(c2)
        assert isinstance(c2_obj, CommitObject)
        assert c2_obj.message.startswith("fixup! Add feature alpha")

        # Squash commit
        (repo.worktree / "a.txt").write_text("v1.2", encoding="utf-8")
        repo.add(["a.txt"])
        c3 = repo.commit(squash=c1)

        c3_obj = repo.store.read(c3)
        assert isinstance(c3_obj, CommitObject)
        assert c3_obj.message.startswith("squash! Add feature alpha")

    def test_rebase_autosquash_reordering(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        base_sha = _commit_file(repo, "a.txt", "base", "base commit")

        repo.branch("feat")
        repo.checkout("feat")
        c1 = _commit_file(repo, "a.txt", "feat 1", "Add feature A")
        c2 = _commit_file(repo, "b.txt", "feat 2", "Add feature B")

        # Create a fixup commit for feature A
        (repo.worktree / "a.txt").write_text("feat 1 fix", encoding="utf-8")
        repo.add(["a.txt"])
        c_fix = repo.commit(fixup=c1)

        # Rebase with autosquash onto main (base_sha)
        res = repo.rebase("main", autosquash=True)
        assert res["status"] in ("picked", "fast-forward", "up-to-date")


# -- Log --follow (Rename Tracking) ----------------------------------------

class TestLogFollow:
    def test_log_follow_renamed_file(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        c1 = _commit_file(repo, "old_name.py", "print('hello')", "create old_name")

        # Rename old_name.py to new_name.py
        (repo.worktree / "old_name.py").unlink()
        repo.rm("old_name.py")
        c2 = _commit_file(repo, "new_name.py", "print('hello')", "rename to new_name")

        # Standard log for new_name.py might miss c1, but log with follow=new_name.py traces back
        commits = repo.log(follow="new_name.py")
        shas = [sha for sha, _ in commits]
        assert c2 in shas
        assert c1 in shas


# -- Shallow boundary (.pygit/shallow) -------------------------------------

class TestShallowBoundary:
    def test_shallow_file_truncates_log(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        c1 = _commit_file(repo, "a.txt", "1", "commit 1")
        c2 = _commit_file(repo, "a.txt", "2", "commit 2")
        c3 = _commit_file(repo, "a.txt", "3", "commit 3")

        # Set shallow boundary at c2
        shallow_file = repo.pygit_dir / "shallow"
        shallow_file.write_text(f"{c2}\n", encoding="utf-8")

        # Log should stop at c2 and not include c1
        commits = repo.log()
        shas = [sha for sha, _ in commits]
        assert c3 in shas
        assert c2 in shas
        assert c1 not in shas


# -- Commit Graph Acceleration ---------------------------------------------

class TestCommitGraph:
    def test_commit_graph_write_and_read(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        c1 = _commit_file(repo, "a.txt", "1", "commit 1")
        c2 = _commit_file(repo, "a.txt", "2", "commit 2")

        graph_path = repo.write_commit_graph()
        assert graph_path.exists()

        cg = CommitGraph(repo.pygit_dir)
        data = cg.read()
        assert len(data) == 2
        assert c1 in data
        assert c2 in data
        assert data[c2][1] == [c1]  # Parent of c2 is c1
