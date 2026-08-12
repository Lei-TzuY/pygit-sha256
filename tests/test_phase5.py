"""Integration tests for Phase 5 pygit features: ancestor syntax (~N/^N), commit --amend, checkout paths, and revert."""

from pathlib import Path
import pytest
from pygit import Repository
from pygit.objects import CommitObject


def _commit_file(repo: Repository, path: str, content: str, message: str) -> str:
    target = repo.worktree / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    repo.add([path])
    return repo.commit(message)


class TestAncestorSyntax:
    def test_tilde_and_caret_ancestor_resolution(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        c1 = _commit_file(repo, "f.txt", "1", "c1")
        c2 = _commit_file(repo, "f.txt", "2", "c2")
        c3 = _commit_file(repo, "f.txt", "3", "c3")

        assert repo.rev_parse("HEAD") == c3
        assert repo.rev_parse("HEAD~1") == c2
        assert repo.rev_parse("HEAD~2") == c1
        assert repo.rev_parse("HEAD^") == c2


class TestCommitAmend:
    def test_commit_amend(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        c1 = _commit_file(repo, "f.txt", "initial", "c1")

        (tmp_path / "f.txt").write_text("updated initial", encoding="utf-8")
        repo.add(["f.txt"])
        amended_sha = repo.commit("amended c1 message", amend=True)

        commit_obj = repo.store.read(amended_sha)
        assert isinstance(commit_obj, CommitObject)
        assert commit_obj.message == "amended c1 message"
        assert commit_obj.parents == []  # Replaced root commit parent list
        assert repo.refs.resolve_head() == amended_sha


class TestCheckoutPaths:
    def test_checkout_paths_restores_file(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        c1 = _commit_file(repo, "app.py", "version 1\n", "c1")
        c2 = _commit_file(repo, "app.py", "version 2\n", "c2")

        # Modify app.py locally in working tree
        (tmp_path / "app.py").write_text("corrupted\n", encoding="utf-8")
        restored = repo.checkout_paths(["app.py"], target="HEAD~1")

        assert "app.py" in restored
        assert (tmp_path / "app.py").read_text(encoding="utf-8") == "version 1\n"


class TestRevert:
    def test_revert_commit(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        _commit_file(repo, "f.txt", "line 1\n", "c1")
        c2 = _commit_file(repo, "f.txt", "line 1\nline 2\n", "c2")

        res = repo.revert(c2)
        assert res["status"] == "reverted"
        assert (tmp_path / "f.txt").read_text(encoding="utf-8") == "line 1\n"

        log_entries = repo.log()
        assert len(log_entries) == 3
        assert 'Revert "c2"' in log_entries[0][1].message
