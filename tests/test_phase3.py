"""Integration tests for Phase 3 pygit features: clean, rev-parse, short SHA resolution, stash show, and operation status."""

from pathlib import Path
import pytest
from pygit import Repository


def _commit_file(repo: Repository, path: str, content: str, message: str) -> str:
    target = repo.worktree / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    repo.add([path])
    return repo.commit(message)


class TestShortSHAResolution:
    def test_short_sha_resolution(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        sha = _commit_file(repo, "file.txt", "content", "initial")

        short_sha = sha[:8]
        resolved = repo.rev_parse(short_sha)
        assert resolved == sha

        # test short sha in show
        show_output = repo.show(short_sha)
        assert f"commit {sha}" in show_output

    def test_ambiguous_short_sha_raises(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        with pytest.raises(KeyError):
            repo.rev_parse("0000")


class TestClean:
    def test_clean_without_force_raises(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        (tmp_path / "untracked.txt").write_text("untracked")

        with pytest.raises(RuntimeError, match="requires force"):
            repo.clean(force=False)

    def test_clean_files(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        _commit_file(repo, "tracked.txt", "tracked", "init")
        (tmp_path / "junk.txt").write_text("junk")

        removed = repo.clean(force=True)
        assert "junk.txt" in removed
        assert not (tmp_path / "junk.txt").exists()
        assert (tmp_path / "tracked.txt").exists()

    def test_clean_directories(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        build_dir = tmp_path / "build" / "output"
        build_dir.mkdir(parents=True)
        (build_dir / "temp.bin").write_text("temp")

        removed = repo.clean(force=True, directories=True)
        assert "build/output/temp.bin" in removed
        assert not (tmp_path / "build").exists()


class TestStashShow:
    def test_stash_show(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        _commit_file(repo, "f.txt", "v1\n", "c1")

        (tmp_path / "f.txt").write_text("v2\n", encoding="utf-8")
        stash_sha = repo.stash_push("WIP changes")

        out = repo.stash_show(stash_sha)
        assert "+v2" in out
        assert "f.txt" in out

        out_stat = repo.stash_show(stash_sha, stat=True)
        assert "f.txt" in out_stat
        assert "1 file changed" in out_stat


class TestStatusOperationInfo:
    def test_status_reports_active_operation(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        _commit_file(repo, "shared.txt", "base\n", "base")
        repo.branch("feature")
        _commit_file(repo, "shared.txt", "feature\n", "feature")
        repo.checkout("main")
        _commit_file(repo, "shared.txt", "main\n", "main")

        # Start a merge that causes a conflict
        repo.merge("feature")
        st = repo.status()
        assert st["operation"] == "merge"
        assert st["conflicts"] == ["shared.txt"]
