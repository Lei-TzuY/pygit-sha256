"""Phase 49 tests: direct index mutation and inspection plumbing."""

from __future__ import annotations

import io
import os
from pathlib import Path

import pytest

from pygit import Repository
from pygit.entrypoint import dispatch
from pygit.index_plumbing import ls_files, refresh_index, update_index
from pygit.objects import BlobObject, TreeObject


def _repo(tmp_path: Path) -> Repository:
    repo = Repository.init(str(tmp_path / "r"))
    tracked = repo.worktree / "tracked.txt"
    tracked.write_text("one\n", encoding="utf-8")
    repo.add(["tracked.txt"])
    return repo


class TestUpdateIndex:
    def test_updates_tracked_worktree_blob(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        old_oid = repo.index.get("tracked.txt").sha
        (repo.worktree / "tracked.txt").write_text("two\n", encoding="utf-8")

        update_index(repo, ["tracked.txt"])

        entry = repo.index.get("tracked.txt")
        assert entry is not None
        assert entry.sha != old_oid
        obj = repo.store.read(entry.sha)
        assert isinstance(obj, BlobObject)
        assert obj.data == b"two\n"

    def test_add_is_required_for_untracked_paths(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        (repo.worktree / "new.txt").write_text("new\n", encoding="utf-8")

        with pytest.raises(KeyError, match="use --add"):
            update_index(repo, ["new.txt"])
        assert "new.txt" not in repo.index

        update_index(repo, ["new.txt"], add=True)
        assert "new.txt" in repo.index

    def test_remove_and_force_remove_semantics(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        tracked = repo.worktree / "tracked.txt"
        tracked.unlink()

        with pytest.raises(FileNotFoundError, match="use --remove"):
            update_index(repo, ["tracked.txt"])
        assert "tracked.txt" in repo.index

        update_index(repo, ["tracked.txt"], remove=True)
        assert "tracked.txt" not in repo.index

        tracked.write_text("back\n", encoding="utf-8")
        update_index(repo, ["tracked.txt"], add=True)
        update_index(repo, ["tracked.txt"], force_remove=True)
        assert tracked.exists()
        assert "tracked.txt" not in repo.index

    def test_cacheinfo_and_index_info(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        blob_oid = repo.store.write(BlobObject(b"cached\n"))

        update_index(
            repo,
            cache_info=[("100644", blob_oid[:12], "virtual.txt")],
        )
        virtual = repo.index.get("virtual.txt")
        assert virtual is not None
        assert virtual.sha == blob_oid
        assert virtual.mode == "100644"

        other_oid = repo.store.write(BlobObject(b"other\n"))
        update_index(
            repo,
            index_info=[
                f"100755 {other_oid} 0\tscripts/tool",
                "0 " + ("0" * 64) + "\tvirtual.txt",
            ],
        )
        assert repo.index.get("scripts/tool").mode == "100755"
        assert "virtual.txt" not in repo.index

    def test_cacheinfo_rejects_wrong_object_type(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        tree_oid = repo.store.write(TreeObject([]))

        with pytest.raises(ValueError, match="requires a blob"):
            update_index(
                repo,
                cache_info=[("100644", tree_oid, "bad.txt")],
            )
        assert "bad.txt" not in repo.index

    def test_failed_batch_does_not_partially_replace_index(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        original = repo.index.get("tracked.txt").sha
        (repo.worktree / "tracked.txt").write_text("changed\n", encoding="utf-8")
        (repo.worktree / "untracked.txt").write_text("new\n", encoding="utf-8")

        with pytest.raises(KeyError, match="use --add"):
            update_index(repo, ["tracked.txt", "untracked.txt"])

        assert repo.index.get("tracked.txt").sha == original
        assert "untracked.txt" not in repo.index

    def test_chmod_changes_index_mode(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        update_index(repo, ["tracked.txt"], chmod="+x")
        assert repo.index.get("tracked.txt").mode == "100755"

        update_index(repo, ["tracked.txt"], chmod="-x")
        assert repo.index.get("tracked.txt").mode == "100644"

    def test_path_escape_and_internal_metadata_are_rejected(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        with pytest.raises(ValueError, match="outside the repository"):
            update_index(repo, ["../escape.txt"], add=True)
        with pytest.raises(ValueError, match="internal metadata"):
            update_index(repo, [".pygit/index"], add=True)

    @pytest.mark.skipif(os.name == "nt", reason="symlink creation is privilege-dependent on Windows")
    def test_symlink_is_staged_as_link_target_bytes(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        link = repo.worktree / "link"
        link.symlink_to("tracked.txt")

        update_index(repo, ["link"], add=True)
        entry = repo.index.get("link")
        assert entry.mode == "120000"
        obj = repo.store.read(entry.sha)
        assert isinstance(obj, BlobObject)
        assert obj.data == b"tracked.txt"


class TestRefreshAndLsFiles:
    def test_refresh_updates_metadata_but_reports_content_changes(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        entry = repo.index.get("tracked.txt")
        old_mtime = entry.mtime
        target = repo.worktree / "tracked.txt"
        os.utime(target, (old_mtime + 5, old_mtime + 5))

        assert refresh_index(repo) == []
        assert repo.index.get("tracked.txt").mtime != old_mtime

        target.write_text("dirty\n", encoding="utf-8")
        assert refresh_index(repo) == ["tracked.txt"]

    def test_ls_files_cached_stage_deleted_modified_and_patterns(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        (repo.worktree / "second.txt").write_text("second\n", encoding="utf-8")
        update_index(repo, ["second.txt"], add=True)

        assert ls_files(repo) == ["second.txt", "tracked.txt"]
        staged = ls_files(repo, stage=True, patterns=["tracked.txt"])
        assert len(staged) == 1
        assert staged[0].endswith(" 0\ttracked.txt")
        assert staged[0].startswith("100644 ")

        (repo.worktree / "tracked.txt").write_text("modified\n", encoding="utf-8")
        (repo.worktree / "second.txt").unlink()
        assert ls_files(repo, modified=True) == ["tracked.txt"]
        assert ls_files(repo, deleted=True) == ["second.txt"]

        with pytest.raises(KeyError, match="did not match"):
            ls_files(repo, patterns=["missing*"], error_unmatch=True)


class TestPhase49CLI:
    def test_update_index_and_ls_files_dispatch(self, tmp_path: Path, monkeypatch, capsys) -> None:
        repo = _repo(tmp_path)
        monkeypatch.chdir(repo.worktree)
        capsys.readouterr()

        (repo.worktree / "new.txt").write_text("new\n", encoding="utf-8")
        assert dispatch(["update-index", "--add", "new.txt"]) == 0
        assert dispatch(["ls-files", "--stage", "new.txt"]) == 0
        output = capsys.readouterr().out.strip()
        assert output.endswith(" 0\tnew.txt")
        assert output.startswith("100644 ")

    def test_index_info_stdin_and_refresh_status(self, tmp_path: Path, monkeypatch, capsys) -> None:
        repo = _repo(tmp_path)
        monkeypatch.chdir(repo.worktree)
        capsys.readouterr()
        oid = repo.store.write(BlobObject(b"stdin\n"))

        monkeypatch.setattr("sys.stdin", io.StringIO(f"100644 {oid}\tstdin.txt\n"))
        assert dispatch(["update-index", "--index-info"]) == 0
        assert "stdin.txt" in Repository(str(repo.worktree)).index

        (repo.worktree / "tracked.txt").write_text("dirty\n", encoding="utf-8")
        assert dispatch(["update-index", "--refresh", "tracked.txt"]) == 1
        assert "tracked.txt: needs update" in capsys.readouterr().err
