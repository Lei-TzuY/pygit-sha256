"""Integration tests for Phase 7 pygit features: Packfiles & Fan-out Index, Git Hooks, and Submodules."""

from pathlib import Path
import pytest
from pygit import Repository
from pygit.pack import PackWriter, PackReader
from pygit.hooks import HookRunner


def _commit_file(repo: Repository, path: str, content: str, message: str) -> str:
    target = repo.worktree / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    repo.add([path])
    return repo.commit(message)


class TestPackEngine:
    def test_repack_and_read_from_packfile(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        c1 = _commit_file(repo, "a.txt", "line 1", "c1")
        c2 = _commit_file(repo, "a.txt", "line 2", "c2")

        # Confirm objects exist as loose objects
        assert repo.store._path_for(c1).exists()

        pack_p, idx_p = repo.repack(delete_loose=True)
        assert pack_p.exists()
        assert idx_p.exists()

        # Loose object deleted
        assert not repo.store._path_for(c1).exists()

        # Object is still readable from packfile
        commit_obj = repo.store.read(c1)
        assert commit_obj.message == "c1"

        log_entries = repo.log()
        assert len(log_entries) == 2


class TestHooksFramework:
    def test_pre_commit_hook_blocks_commit_on_failure(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        hook_path = repo.pygit_dir / "hooks" / "pre-commit.py"
        hook_path.parent.mkdir(parents=True, exist_ok=True)
        hook_path.write_text("import sys\nsys.exit(1)\n", encoding="utf-8")

        (tmp_path / "f.txt").write_text("hello", encoding="utf-8")
        repo.add(["f.txt"])

        with pytest.raises(RuntimeError, match="pre-commit hook failed"):
            repo.commit("should fail")


class TestSubmodules:
    def test_add_and_list_submodule(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        sub_path = repo.submodule_add("https://github.com/example/libfoo.git", "libs/libfoo")

        assert sub_path == "libs/libfoo"
        assert (tmp_path / ".pygitmodules").exists()

        subs = repo.submodule_list()
        assert len(subs) == 1
        assert subs[0] == ("libs/libfoo", "libs/libfoo", "https://github.com/example/libfoo.git")
