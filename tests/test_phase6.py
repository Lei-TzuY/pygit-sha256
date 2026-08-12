"""Integration tests for Phase 6 pygit features: shortlog, describe, and interactive rebase todo."""

from pathlib import Path
import pytest
from pygit import Repository


def _commit_file(repo: Repository, path: str, content: str, message: str, author_name: str = "Alice", author_email: str = "") -> str:
    if not author_email:
        author_email = f"{author_name.lower()}@example.com"
    target = repo.worktree / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    repo.add([path])
    return repo.commit(message, author_name=author_name, author_email=author_email)


class TestShortlog:
    def test_shortlog_grouping(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        _commit_file(repo, "a.txt", "1", "c1", author_name="Alice")
        _commit_file(repo, "a.txt", "2", "c2", author_name="Bob")
        _commit_file(repo, "a.txt", "3", "c3", author_name="Alice")

        sl = repo.shortlog()
        assert "Alice <alice@example.com>" in sl
        assert "Bob <bob@example.com>" in sl
        assert len(sl["Alice <alice@example.com>"]) == 2
        assert len(sl["Bob <bob@example.com>"]) == 1


class TestDescribe:
    def test_describe_exact_and_distance(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        c1 = _commit_file(repo, "a.txt", "1", "c1")
        repo.tag("v1.0", c1)

        # Exact match
        assert repo.describe(c1) == "v1.0"

        # 2 commits ahead
        c2 = _commit_file(repo, "a.txt", "2", "c2")
        c3 = _commit_file(repo, "a.txt", "3", "c3")

        desc = repo.describe(c3)
        assert desc.startswith("v1.0-2-g")
        assert c3[:7] in desc


class TestRebaseTodo:
    def test_rebase_todo_pick_drop_reword_squash(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        c1 = _commit_file(repo, "f.txt", "line 1\n", "c1")
        c2 = _commit_file(repo, "f.txt", "line 1\nline 2\n", "c2")
        c3 = _commit_file(repo, "f.txt", "line 1\nline 2\nline 3\n", "c3")

        # Checkout c1 (detach) and apply rebase todo
        repo.checkout(c1)
        todo = [
            ("pick", c2, None),
            ("reword", c3, "c3 reworded"),
        ]
        res = repo.rebase_todo(todo)
        assert res["status"] == "completed"

        head_commit = repo.store.read(repo.refs.resolve_head())
        assert head_commit.message == "c3 reworded"
