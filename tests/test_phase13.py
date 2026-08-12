"""Integration tests for Phase 13 pygit features: External Diff/Merge Tool Helper & Commit Template Engine."""

from pathlib import Path
import pytest
from pygit import Repository


def _commit_file(repo: Repository, path: str, content: str, message: str) -> str:
    target = repo.worktree / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    repo.add([path])
    return repo.commit(message)


class TestPhase13ToolsAndTemplates:
    def test_difftool_and_mergetool(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        _commit_file(repo, "f.txt", "line 1\n", "c1")
        (tmp_path / "f.txt").write_text("line 1\nline 2\n", encoding="utf-8")

        diff_lines = repo.difftool()
        assert len(diff_lines) > 0
        assert any("+ line 2" in l for l in diff_lines)

        merge_statuses = repo.mergetool()
        assert merge_statuses == []

    def test_commit_message_template(self, tmp_path):
        repo = Repository.init(str(tmp_path))

        tmpl_file = tmp_path / "my_tmpl.txt"
        tmpl_file.write_text("[FEAT] Ticket #123", encoding="utf-8")

        (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
        repo.add(["a.txt"])

        c1 = repo.commit(message="Implement feature", template=str(tmpl_file))
        commit_obj = repo.store.read(c1)
        assert "[FEAT] Ticket #123" in commit_obj.message
        assert "Implement feature" in commit_obj.message

    def test_default_pygitmessage_template(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        (tmp_path / ".pygitmessage").write_text("[CHORE] Default template msg", encoding="utf-8")

        (tmp_path / "b.txt").write_text("world", encoding="utf-8")
        repo.add(["b.txt"])

        c1 = repo.commit()
        commit_obj = repo.store.read(c1)
        assert "[CHORE] Default template msg" in commit_obj.message
