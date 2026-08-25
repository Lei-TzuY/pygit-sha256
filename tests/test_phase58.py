"""Phase 58 tests: diff-tree, diff-index, and diff-files plumbing."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from pygit.diff_plumbing import (
    ZERO_OID,
    diff_files,
    diff_index,
    diff_tree,
    format_diff_entries,
)
from pygit.index import IndexEntry
from pygit.objects import BlobObject, CommitObject, Identity, TreeEntry, TreeObject
from pygit.repo import Repository


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "r"))


def _commit(repo: Repository, tree: str, parents: list[str], message: str, timestamp: int) -> str:
    ident = Identity("Tester", "tester@example.com", timestamp, "+0000")
    return repo.store.write(
        CommitObject(
            tree=tree,
            parents=parents,
            author=ident,
            committer=ident,
            message=message,
        )
    )


def _blob(repo: Repository, data: bytes) -> str:
    return repo.store.write(BlobObject(data))


def _tree(repo: Repository, entries: list[TreeEntry]) -> str:
    return repo.store.write(TreeObject(entries))


def _history(repo: Repository) -> tuple[str, str, str, str, str]:
    old = _blob(repo, b"old\n")
    new = _blob(repo, b"new\n")
    added = _blob(repo, b"added\n")
    removed = _blob(repo, b"removed\n")
    target = _blob(repo, b"destination")

    left_tree = _tree(
        repo,
        [
            TreeEntry("100644", "change.txt", old),
            TreeEntry("100644", "removed.txt", removed),
            TreeEntry("100644", "type.txt", target),
        ],
    )
    root = _commit(repo, left_tree, [], "root", 1)

    link_blob = _blob(repo, b"change.txt")
    right_tree = _tree(
        repo,
        [
            TreeEntry("100644", "added.txt", added),
            TreeEntry("100644", "change.txt", new),
            TreeEntry("120000", "type.txt", link_blob),
        ],
    )
    tip = _commit(repo, right_tree, [root], "tip", 2)
    repo.refs.set_branch("main", tip)
    repo.refs.set_head_symbolic("main")
    return root, tip, old, new, added


def _run(repo: Repository, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


class TestDiffTree:
    def test_two_treeish_comparison_reports_add_delete_modify_and_type(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        root, tip, _, new, added = _history(repo)

        entries = diff_tree(repo, root, tip)
        assert [(entry.path, entry.status) for entry in entries] == [
            ("added.txt", "A"),
            ("change.txt", "M"),
            ("removed.txt", "D"),
            ("type.txt", "T"),
        ]
        assert entries[0].old_oid == ZERO_OID
        assert entries[0].new_oid == added
        assert entries[1].new_oid == new
        assert entries[2].new_oid == ZERO_OID
        assert entries[3].old_mode == "100644"
        assert entries[3].new_mode == "120000"

    def test_single_commit_uses_first_parent_and_root_mode(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        root, tip, _, _, _ = _history(repo)

        assert diff_tree(repo, tip) == diff_tree(repo, root, tip)
        assert diff_tree(repo, root) == []
        root_entries = diff_tree(repo, root, root=True)
        assert {entry.status for entry in root_entries} == {"A"}
        assert {entry.path for entry in root_entries} == {"change.txt", "removed.txt", "type.txt"}

    def test_pathspec_filters_exact_prefix_and_glob(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        root, tip, _, _, _ = _history(repo)

        assert [entry.path for entry in diff_tree(repo, root, tip, patterns=["change.txt"])] == ["change.txt"]
        assert [entry.path for entry in diff_tree(repo, root, tip, patterns=["*.txt"])] == [
            "added.txt",
            "change.txt",
            "removed.txt",
            "type.txt",
        ]


class TestIndexAndWorktreeDiffs:
    def test_diff_index_cached_compares_tree_to_index(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        root, _, _, new, added = _history(repo)
        repo.index.entries = {
            "change.txt": IndexEntry("change.txt", new, "100644"),
            "added.txt": IndexEntry("added.txt", added, "100644"),
        }
        repo.index.save()

        entries = diff_index(repo, root, cached=True)
        assert [(entry.path, entry.status) for entry in entries] == [
            ("added.txt", "A"),
            ("change.txt", "M"),
            ("removed.txt", "D"),
            ("type.txt", "D"),
        ]

    def test_diff_files_detects_content_mode_and_deletion(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        old = _blob(repo, b"old\n")
        gone = _blob(repo, b"gone\n")
        repo.index.entries = {
            "script": IndexEntry("script", old, "100644"),
            "gone.txt": IndexEntry("gone.txt", gone, "100644"),
        }
        repo.index.save()
        (repo.worktree / "script").write_bytes(b"changed\n")
        if os.name != "nt":
            (repo.worktree / "script").chmod(0o755)

        entries = diff_files(repo)
        assert [(entry.path, entry.status) for entry in entries] == [
            ("gone.txt", "D"),
            ("script", "M"),
        ]
        script = entries[1]
        assert script.new_oid == BlobObject(b"changed\n").hash()
        if os.name != "nt":
            assert script.new_mode == "100755"

    def test_diff_index_default_compares_tree_to_final_tracked_worktree(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        root, _, _, new, _ = _history(repo)
        repo.index.entries = {"change.txt": IndexEntry("change.txt", new, "100644")}
        repo.index.save()
        (repo.worktree / "change.txt").write_bytes(b"worktree\n")

        entries = diff_index(repo, root)
        change = next(entry for entry in entries if entry.path == "change.txt")
        assert change.status == "M"
        assert change.new_oid == BlobObject(b"worktree\n").hash()

    def test_corrupt_index_path_cannot_escape_worktree(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        blob = _blob(repo, b"x")
        repo.index.entries = {"../outside": IndexEntry("../outside", blob, "100644")}

        with pytest.raises(ValueError, match="invalid repository path"):
            diff_files(repo)

    @pytest.mark.skipif(os.name == "nt", reason="symlink creation is not reliably available on Windows")
    def test_symlink_target_bytes_are_compared(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        old = _blob(repo, b"old-target")
        repo.index.entries = {"link": IndexEntry("link", old, "120000")}
        repo.index.save()
        os.symlink("new-target", repo.worktree / "link")

        entries = diff_files(repo)
        assert len(entries) == 1
        assert entries[0].new_mode == "120000"
        assert entries[0].new_oid == BlobObject(b"new-target").hash()


class TestFormattingAndCli:
    def test_formatters_support_raw_name_status_name_only_and_nul(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        root, tip, _, _, _ = _history(repo)
        entries = diff_tree(repo, root, tip, patterns=["change.txt"])

        raw = format_diff_entries(entries).decode()
        assert raw.startswith(":100644 100644 ")
        assert raw.endswith(" M\tchange.txt\n")
        assert format_diff_entries(entries, name_status=True) == b"M\tchange.txt\n"
        assert format_diff_entries(entries, name_only=True, nul_terminated=True) == b"change.txt\x00"

    def test_cli_routes_all_three_commands_and_exit_status(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        root, tip, _, new, _ = _history(repo)
        repo.index.entries = {"change.txt": IndexEntry("change.txt", new, "100644")}
        repo.index.save()
        (repo.worktree / "change.txt").write_bytes(b"dirty\n")

        tree = _run(repo, "diff-tree", "--name-status", root, tip, "--", "change.txt")
        assert tree.returncode == 0, tree.stderr.decode()
        assert tree.stdout == b"M\tchange.txt\n"

        cached = _run(repo, "diff-index", "--cached", "--name-only", root, "--", "change.txt")
        assert cached.returncode == 0, cached.stderr.decode()
        assert cached.stdout == b"change.txt\n"

        files = _run(repo, "diff-files", "--exit-code", "--name-only", "--", "change.txt")
        assert files.returncode == 1
        assert files.stdout == b"change.txt\n"

        quiet = _run(repo, "diff-files", "--quiet", "--", "change.txt")
        assert quiet.returncode == 1
        assert quiet.stdout == b""
