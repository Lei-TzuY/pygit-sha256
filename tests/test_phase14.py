"""
tests/test_phase14.py
=====================
Phase 14 tests: config, grep, notes, line-level diff3, cmd_graph, hooks.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from pygit import Repository
from pygit.config import GitConfig
from pygit.notes import NoteStore
from pygit.objects import BlobObject, CommitObject


# -- helpers ---------------------------------------------------------------

def _commit_file(repo: Repository, name: str, content: str, msg: str) -> str:
    """Write *content* to *name*, stage, and commit."""
    path = repo.worktree / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    repo.add([name])
    return repo.commit(msg, author_name="Tester", author_email="t@e.com")


# -- config ----------------------------------------------------------------

class TestConfig:
    def test_config_set_get_list_unset(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))

        # set
        repo.config_set("user", "name", "Alice")
        repo.config_set("user", "email", "alice@example.com")
        repo.config_set("core", "eol", "lf")

        # get
        assert repo.config_get("user", "name") == "Alice"
        assert repo.config_get("user", "email") == "alice@example.com"
        assert repo.config_get("core", "eol") == "lf"
        assert repo.config_get("core", "missing") is None

        # list
        entries = repo.config_list()
        assert ("user", "name", "Alice") in entries
        assert ("core", "eol", "lf") in entries

        # unset
        repo.config_unset("user", "name")
        assert repo.config_get("user", "name") is None

        # persistence: re-open config and verify
        cfg2 = GitConfig(repo.pygit_dir)
        assert cfg2.get("user", "email") == "alice@example.com"
        assert cfg2.get("user", "name") is None


# -- grep ------------------------------------------------------------------

class TestGrep:
    def test_grep_worktree_search(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "hello.py", "print('hello world')\nprint('goodbye')", "init")

        results = repo.grep("hello")
        assert any("hello.py" in r and "hello" in r for r in results)

        results_ci = repo.grep("HELLO", ignore_case=True)
        assert any("hello.py" in r for r in results_ci)

        results_count = repo.grep("print", count_only=True)
        assert any("hello.py:2" == r for r in results_count)

    def test_grep_commit_search(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        sha1 = _commit_file(repo, "a.txt", "alpha\nbeta\ngamma", "v1")
        sha2 = _commit_file(repo, "a.txt", "alpha\ndelta\ngamma", "v2")

        results_v1 = repo.grep("beta", target=sha1)
        assert any("beta" in r for r in results_v1)

        results_v2 = repo.grep("beta", target=sha2)
        assert len(results_v2) == 0

        results_v2_delta = repo.grep("delta", target=sha2)
        assert any("delta" in r for r in results_v2_delta)


# -- notes -----------------------------------------------------------------

class TestNotes:
    def test_notes_add_show_list_remove(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        sha = _commit_file(repo, "f.txt", "data", "commit")

        # add note
        note_sha = repo.notes_add(sha, "This is a note.")
        assert note_sha

        # show note
        text = repo.notes_show(sha)
        assert text == "This is a note."

        # list notes
        entries = repo.notes_list()
        assert len(entries) == 1
        assert entries[0][0] == sha

        # remove note
        assert repo.notes_remove(sha) is True
        assert repo.notes_show(sha) is None
        assert repo.notes_remove(sha) is False


# -- line-level diff3 merge ------------------------------------------------

class TestDiff3Merge:
    def test_line_level_merge_auto_resolve(self, tmp_path: Path) -> None:
        """Non-overlapping edits from two branches should auto-merge."""
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "f.txt", "line1\nline2\nline3\nline4\nline5\n", "base")

        # branch A: change line 1
        repo.branch("branchA")
        repo.checkout("branchA")
        _commit_file(repo, "f.txt", "CHANGED1\nline2\nline3\nline4\nline5\n", "change line 1")

        # branch B from main: change line 5
        repo.checkout("main")
        repo.branch("branchB")
        repo.checkout("branchB")
        _commit_file(repo, "f.txt", "line1\nline2\nline3\nline4\nCHANGED5\n", "change line 5")

        # merge branchA into branchB -- should auto-resolve
        result = repo.merge("branchA")
        content = (repo.worktree / "f.txt").read_text(encoding="utf-8")
        assert "CHANGED1" in content
        assert "CHANGED5" in content
        assert "<<<<<<" not in content

    def test_line_level_merge_conflict(self, tmp_path: Path) -> None:
        """Overlapping edits should produce conflict markers."""
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "f.txt", "line1\nline2\nline3\n", "base")

        repo.branch("feat")
        repo.checkout("feat")
        _commit_file(repo, "f.txt", "line1\nAA\nline3\n", "feat change")

        repo.checkout("main")
        _commit_file(repo, "f.txt", "line1\nBB\nline3\n", "main change")

        result = repo.merge("feat")
        assert result["status"] == "conflicts"
        content = (repo.worktree / "f.txt").read_text(encoding="utf-8")
        assert "<<<<<<<" in content
        assert "=======" in content
        assert ">>>>>>>" in content


# -- cmd_graph -------------------------------------------------------------

class TestCmdGraph:
    def test_cmd_graph_runs(self, tmp_path: Path) -> None:
        """Verify render_graph doesn't crash (was a NameError before fix)."""
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "a.txt", "hello", "first")
        _commit_file(repo, "a.txt", "world", "second")

        lines = repo.render_graph(max_count=5)
        assert isinstance(lines, list)
        assert len(lines) >= 1


# -- hooks -----------------------------------------------------------------

class TestHooksIntegration:
    def test_commit_msg_hook(self, tmp_path: Path) -> None:
        """commit-msg hook can modify the commit message."""
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "a.txt", "hello", "initial")

        # Install a commit-msg hook that appends a signature
        hooks_dir = repo.pygit_dir / "hooks"
        hooks_dir.mkdir(exist_ok=True)
        hook = hooks_dir / "commit-msg.py"
        hook.write_text(
            "import sys\n"
            "msg_file = sys.argv[1]\n"
            "with open(msg_file, 'r') as f:\n"
            "    msg = f.read()\n"
            "with open(msg_file, 'w') as f:\n"
            "    f.write(msg.rstrip() + '\\n\\nSigned-off-by: Bot')\n",
            encoding="utf-8",
        )

        (repo.worktree / "b.txt").write_text("data", encoding="utf-8")
        repo.add(["b.txt"])
        sha = repo.commit("test msg", author_name="T", author_email="t@e.com")

        obj = repo.store.read(sha)
        assert isinstance(obj, CommitObject)
        assert "Signed-off-by: Bot" in obj.message

    def test_post_checkout_hook(self, tmp_path: Path) -> None:
        """post-checkout hook is invoked after checkout."""
        repo = Repository.init(str(tmp_path / "r"))
        _commit_file(repo, "a.txt", "hello", "on main")

        repo.branch("dev")

        # Install a post-checkout hook that writes a marker file
        hooks_dir = repo.pygit_dir / "hooks"
        hooks_dir.mkdir(exist_ok=True)
        hook = hooks_dir / "post-checkout.py"
        marker = repo.worktree / ".checkout-marker"
        hook.write_text(
            "import sys\n"
            f"with open(r'{marker}', 'w') as f:\n"
            "    f.write(f'{sys.argv[1][:8]} {sys.argv[2][:8]} {sys.argv[3]}')\n",
            encoding="utf-8",
        )

        repo.checkout("dev")
        assert marker.exists()
        content = marker.read_text()
        # Should contain old_sha, new_sha, branch_flag
        assert "1" in content  # branch_flag should be "1"
