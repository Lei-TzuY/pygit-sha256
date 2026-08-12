"""Integration tests for Phase 2 pygit additions: show, blame, ls-files, diff between refs, log filters, and branch rename."""

from pathlib import Path
import pytest
from pygit import Repository
from pygit.objects import CommitObject


def _commit_file(repo: Repository, path: str, content: str, message: str, author_name: str = "Alice", author_email: str = "") -> str:
    if not author_email:
        author_email = f"{author_name.lower()}@example.com"
    target = repo.worktree / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    repo.add([path])
    return repo.commit(message, author_name=author_name, author_email=author_email)


class TestDiffBetweenRefsAndStat:
    def test_diff_two_commits(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        sha1 = _commit_file(repo, "f.txt", "line 1\n", "c1")
        sha2 = _commit_file(repo, "f.txt", "line 1\nline 2\n", "c2")

        diff = repo.diff(from_ref=sha1, to_ref=sha2)
        assert "+line 2" in diff
        assert "a/f.txt" in diff

    def test_diff_commit_vs_worktree(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        sha1 = _commit_file(repo, "f.txt", "line 1\n", "c1")
        (tmp_path / "f.txt").write_text("line 1\nline 2\n", encoding="utf-8")

        diff = repo.diff(from_ref=sha1)
        assert "+line 2" in diff

    def test_diff_stat(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        _commit_file(repo, "f.txt", "line 1\n", "c1")
        (tmp_path / "f.txt").write_text("line 1\nline 2\n", encoding="utf-8")

        stat_out = repo.diff(stat=True)
        assert "f.txt" in stat_out
        assert "1 file changed, 1 insertion(+)" in stat_out


class TestShow:
    def test_show_head(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        sha = _commit_file(repo, "a.txt", "hello\n", "Initial commit", author_name="Bob")

        output = repo.show("HEAD")
        assert f"commit {sha}" in output
        assert "Author: Bob <bob@example.com>" in output
        assert "Initial commit" in output
        assert "+hello" in output


class TestLsFiles:
    def test_ls_files_basic(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        (tmp_path / "b.txt").write_text("b")
        (tmp_path / "a.txt").write_text("a")
        repo.add(["a.txt", "b.txt"])

        files = repo.ls_files()
        assert files == ["a.txt", "b.txt"]

    def test_ls_files_stage(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        (tmp_path / "a.txt").write_text("hello")
        repo.add(["a.txt"])

        lines = repo.ls_files(stage=True)
        assert len(lines) == 1
        assert lines[0].startswith("100644 ")
        assert "\ta.txt" in lines[0]


class TestBranchRename:
    def test_branch_rename(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        _commit_file(repo, "a.txt", "hello", "c1")

        repo.branch("feature")
        assert "feature" in repo.refs.list_branches()

        repo.branch("feature", rename="topic")
        assert "feature" not in repo.refs.list_branches()
        assert "topic" in repo.refs.list_branches()

    def test_branch_rename_current_updates_head(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        _commit_file(repo, "a.txt", "hello", "c1")

        assert repo.refs.current_branch() == "main"
        repo.branch("main", rename="trunk")
        assert repo.refs.current_branch() == "trunk"


class TestLogEnhancements:
    def test_log_all_branches(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        _commit_file(repo, "a.txt", "base", "base")

        repo.branch("side")
        repo.checkout("side")
        c_side = _commit_file(repo, "side.txt", "side content", "side commit")

        repo.checkout("main")
        c_main = _commit_file(repo, "main.txt", "main content", "main commit")

        log_head = [sha for sha, _ in repo.log()]
        assert c_main in log_head
        assert c_side not in log_head

        log_all = [sha for sha, _ in repo.log(all_branches=True)]
        assert c_main in log_all
        assert c_side in log_all

    def test_log_filters(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        _commit_file(repo, "a.txt", "1", "Fix bug in parser", author_name="Alice")
        _commit_file(repo, "a.txt", "2", "Add feature X", author_name="Bob")

        alice_log = repo.log(author="Alice")
        assert len(alice_log) == 1
        assert alice_log[0][1].author.name == "Alice"

        bug_log = repo.log(grep="bug")
        assert len(bug_log) == 1
        assert "parser" in bug_log[0][1].message


class TestBlame:
    def test_blame_basic(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        _commit_file(repo, "code.py", "line 1\nline 2\n", "c1", author_name="Alice")
        _commit_file(repo, "code.py", "line 1\nline 2 modified\nline 3\n", "c2", author_name="Bob")

        blame_lines = repo.blame("code.py")
        assert len(blame_lines) == 3
        assert "Alice" in blame_lines[0]
        assert "line 1" in blame_lines[0]
        assert "Bob" in blame_lines[1]
        assert "line 2 modified" in blame_lines[1]
        assert "Bob" in blame_lines[2]
        assert "line 3" in blame_lines[2]
