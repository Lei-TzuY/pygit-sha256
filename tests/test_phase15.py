"""
tests/test_phase15.py
=====================
Phase 15 tests: stash apply/drop/branch, checkout --orphan, log since/until/patch,
sparse-checkout, and SSH process runner.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from pygit import Repository
from pygit.remote_ssh import SSHRemoteClient, parse_ssh_url
from pygit.sparse import SparseCheckout


# -- helpers ---------------------------------------------------------------

def _commit_file(repo: Repository, name: str, content: str, msg: str) -> str:
    """Write *content* to *name*, stage, and commit."""
    path = repo.worktree / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    repo.add([name])
    return repo.commit(msg, author_name="Tester", author_email="t@e.com")


# -- Stash extensions (apply, drop, branch) ---------------------------------

class TestStashExtensions:
    def test_stash_apply_keeps_stash(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "a.txt", "v1", "commit 1")

        (repo.worktree / "a.txt").write_text("v2-dirty", encoding="utf-8")
        stash_sha = repo.stash_push("wip")

        # Apply stash -- should restore file content but keep refs/stash
        repo.stash_apply(0)
        assert (repo.worktree / "a.txt").read_text() == "v2-dirty"
        assert repo.refs.get_stash() == stash_sha

    def test_stash_drop(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "a.txt", "v1", "commit 1")

        (repo.worktree / "a.txt").write_text("stash1", encoding="utf-8")
        s1 = repo.stash_push("stash 1")

        (repo.worktree / "a.txt").write_text("stash2", encoding="utf-8")
        s2 = repo.stash_push("stash 2")

        stashes = repo.stash_list()
        assert len(stashes) == 2

        # Drop top stash
        dropped = repo.stash_drop(0)
        assert dropped == s2
        assert repo.refs.get_stash() == s1

        # Drop remaining stash
        repo.stash_drop(0)
        assert repo.refs.get_stash() is None

    def test_stash_branch(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "a.txt", "base", "base commit")

        (repo.worktree / "a.txt").write_text("stash-mod", encoding="utf-8")
        repo.stash_push("wip branch")

        repo.stash_branch("feature-branch", 0)
        assert repo.refs.current_branch() == "feature-branch"
        assert (repo.worktree / "a.txt").read_text() == "stash-mod"
        assert repo.refs.get_stash() is None


# -- Checkout --orphan ------------------------------------------------------

class TestCheckoutOrphan:
    def test_checkout_orphan(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "a.txt", "v1", "first commit")

        # Checkout orphan branch
        repo.checkout("fresh-start", orphan=True)
        assert repo.refs.current_branch() == "fresh-start"

        # Commit on orphan branch -- commit should have no parents
        sha = _commit_file(repo, "b.txt", "orphan content", "orphan root")
        commit_obj = repo.store.read(sha)
        assert len(commit_obj.parents) == 0


# -- Log since/until/patch --------------------------------------------------

class TestLogExtensions:
    def test_log_date_filtering(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        t0 = time.time()
        _commit_file(repo, "a.txt", "c1", "first")
        t1 = time.time() + 1.0

        # Filter log with since/until timestamps
        commits_all = repo.log()
        assert len(commits_all) == 1

        commits_future = repo.log(since=str(t1 + 10))
        assert len(commits_future) == 0

        commits_past = repo.log(until=str(t1 + 10))
        assert len(commits_past) == 1


# -- Sparse checkout --------------------------------------------------------

class TestSparseCheckout:
    def test_sparse_checkout_rules(self, tmp_path: Path) -> None:
        sc = SparseCheckout(tmp_path / ".pygit")
        assert not sc.enabled

        sc.patterns = ["src/", "!src/ignored.txt", "*.py"]
        sc.save()
        assert sc.enabled

        assert sc.matches("src/main.py")
        assert not sc.matches("src/ignored.txt")
        assert sc.matches("app.py")

        filtered = sc.filter_paths({"src/main.py", "src/ignored.txt", "data.bin"})
        assert filtered == {"src/main.py"}

        sc.disable()
        assert not sc.enabled

    def test_repo_sparse_checkout(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "src/main.py", "print(1)", "c1")
        _commit_file(repo, "docs/readme.txt", "info", "c2")

        repo.sparse_checkout_set(["src/"])
        patterns = repo.sparse_checkout_list()
        assert patterns == ["src/"]

        # Checkout -- docs/readme.txt should not be written to worktree
        repo.checkout("main")
        assert (repo.worktree / "src" / "main.py").exists()
        assert not (repo.worktree / "docs" / "readme.txt").exists()

        repo.sparse_checkout_disable()
        assert (repo.worktree / "docs" / "readme.txt").exists()


# -- SSH Remote Client ------------------------------------------------------

class TestSSHRemoteClient:
    def test_ssh_client_url_parsing_and_command(self) -> None:
        url = "git@github.com:user/myrepo.git"
        parsed = parse_ssh_url(url)
        assert parsed is not None
        assert parsed.user == "git"
        assert parsed.host == "github.com"
        assert parsed.path == "user/myrepo.git"

        client = SSHRemoteClient(url)
        cmd = client.build_ssh_command("git-upload-pack")
        assert cmd == ["ssh", "git@github.com", "git-upload-pack 'user/myrepo.git'"]
