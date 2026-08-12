"""Integration tests for Phase 10 pygit features: Linked Worktrees & End-of-Line (EOL) Normalizer Filters."""

from pathlib import Path
import pytest
from pygit import Repository
from pygit.eol import EOLNormalizer
from pygit.worktree import WorktreeManager


def _commit_file(repo: Repository, path: str, content: str, message: str) -> str:
    target = repo.worktree / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    repo.add([path])
    return repo.commit(message)


class TestEOLNormalizer:
    def test_crlf_normalized_to_lf_in_store(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        (tmp_path / ".pygitattributes").write_text("*.txt text=auto\n", encoding="utf-8")

        # Write file with Windows CRLF (\r\n) line endings
        crlf_content = "line1\r\nline2\r\n"
        (tmp_path / "crlf.txt").write_bytes(crlf_content.encode("utf-8"))

        repo.add(["crlf.txt"])
        c1 = repo.commit("add crlf file")

        entry = repo.index.entries["crlf.txt"]
        blob_obj = repo.store.read(entry.sha)
        stored_bytes = blob_obj.data

        # Store contains normalized LF (\n) bytes
        assert b"\r\n" not in stored_bytes
        assert stored_bytes == b"line1\nline2\n"


class TestWorktreeManager:
    def test_add_list_remove_worktree(self, tmp_path):
        repo = Repository.init(str(tmp_path / "main_repo"))
        _commit_file(repo, "app.py", "v1", "c1")

        wt_dir = tmp_path / "feat_wt"
        added_path = repo.worktree_add(str(wt_dir), branch="feature")

        assert added_path.exists()
        assert (added_path / ".pygit").exists()

        wts = repo.worktree_list()
        assert len(wts) == 2
        names = [w[0] for w in wts]
        assert "main" in names
        assert "feat_wt" in names

        removed = repo.worktree_remove(str(wt_dir))
        assert removed is True
        wts_after = repo.worktree_list()
        assert len(wts_after) == 1
