"""Phase 59 tests: diff-tree, diff-index, and diff-files plumbing."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from pygit.diff_plumbing import ZERO_OID, diff_files, diff_index, diff_tree, format_diff_entries
from pygit.index import IndexEntry
from pygit.objects import BlobObject, CommitObject, Identity, TagObject, TreeEntry, TreeObject
from pygit.repo import Repository


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "r"))


def _commit(repo: Repository, tree: str, parents: list[str], message: str, timestamp: int) -> str:
    ident = Identity("Tester", "tester@example.com", timestamp, "+0000")
    return repo.store.write(CommitObject(tree=tree, parents=parents, author=ident, committer=ident, message=message))


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
    left_tree = _tree(repo, [
        TreeEntry("100644", "change.txt", old),
        TreeEntry("100644", "removed.txt", removed),
        TreeEntry("100644", "type.txt", target),
    ])
    root = _commit(repo, left_tree, [], "root", 1)
    link_blob = _blob(repo, b"change.txt")
    right_tree = _tree(repo, [
        TreeEntry("100644", "added.txt", added),
        TreeEntry("100644", "change.txt", new),
        TreeEntry("120000", "type.txt", link_blob),
    ])
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


def test_diff_tree_reports_add_delete_modify_and_type(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    root, tip, _, new, added = _history(repo)
    entries = diff_tree(repo, root, tip)
    assert [(entry.path, entry.status) for entry in entries] == [
        ("added.txt", "A"), ("change.txt", "M"), ("removed.txt", "D"), ("type.txt", "T")
    ]
    assert entries[0].old_oid == ZERO_OID and entries[0].new_oid == added
    assert entries[1].new_oid == new
    assert entries[2].new_oid == ZERO_OID
    assert (entries[3].old_mode, entries[3].new_mode) == ("100644", "120000")


def test_single_diff_tree_first_parent_root_tag_and_shallow(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    root, tip, _, _, _ = _history(repo)
    assert diff_tree(repo, tip) == diff_tree(repo, root, tip)
    assert diff_tree(repo, root) == []
    assert {entry.status for entry in diff_tree(repo, root, root=True)} == {"A"}

    ident = Identity("Tester", "tester@example.com", 3, "+0000")
    tag = repo.store.write(TagObject(target_sha=tip, target_type=b"commit", tag_name="v1", tagger=ident, message="release"))
    repo.refs.set_tag("v1", tag)
    assert diff_tree(repo, "v1") == diff_tree(repo, root, tip)

    (repo.pygit_dir / "shallow").write_text(tip + "\n", encoding="utf-8")
    assert diff_tree(repo, "HEAD") == []
    assert [(entry.path, entry.status) for entry in diff_tree(repo, "HEAD", root=True)] == [
        ("added.txt", "A"), ("change.txt", "A"), ("type.txt", "A")
    ]


def test_diff_tree_pathspecs_and_packed_only_abbreviations(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    root, tip, _, _, _ = _history(repo)
    assert [entry.path for entry in diff_tree(repo, root, tip, patterns=["change.txt"])] == ["change.txt"]
    assert [entry.path for entry in diff_tree(repo, root, tip, patterns=["*.txt"])] == [
        "added.txt", "change.txt", "removed.txt", "type.txt"
    ]
    repo.repack(delete_loose=True)
    assert [(entry.path, entry.status) for entry in diff_tree(repo, root[:12], tip[:12])] == [
        ("added.txt", "A"), ("change.txt", "M"), ("removed.txt", "D"), ("type.txt", "T")
    ]


def test_diff_index_cached_compares_tree_to_index(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    root, _, _, new, added = _history(repo)
    repo.index.entries = {
        "change.txt": IndexEntry("change.txt", new, "100644"),
        "added.txt": IndexEntry("added.txt", added, "100644"),
    }
    repo.index.save()
    assert [(entry.path, entry.status) for entry in diff_index(repo, root, cached=True)] == [
        ("added.txt", "A"), ("change.txt", "M"), ("removed.txt", "D"), ("type.txt", "D")
    ]


def test_diff_files_detects_content_mode_deletion_and_worktree_diff_index(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    root, _, old, new, _ = _history(repo)
    gone = _blob(repo, b"gone\n")
    repo.index.entries = {
        "change.txt": IndexEntry("change.txt", new, "100644"),
        "gone.txt": IndexEntry("gone.txt", gone, "100644"),
    }
    repo.index.save()
    (repo.worktree / "change.txt").write_bytes(b"worktree\n")
    if os.name != "nt":
        (repo.worktree / "change.txt").chmod(0o755)

    entries = diff_files(repo)
    assert [(entry.path, entry.status) for entry in entries] == [("change.txt", "M"), ("gone.txt", "D")]
    assert entries[0].new_oid == BlobObject(b"worktree\n").hash()
    if os.name != "nt":
        assert entries[0].new_mode == "100755"

    tree_entries = diff_index(repo, root)
    change = next(entry for entry in tree_entries if entry.path == "change.txt")
    assert change.old_oid == old
    assert change.new_oid == BlobObject(b"worktree\n").hash()


def test_corrupt_index_path_cannot_escape_worktree(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    blob = _blob(repo, b"x")
    repo.index.entries = {"../outside": IndexEntry("../outside", blob, "100644")}
    with pytest.raises(ValueError, match="invalid repository path"):
        diff_files(repo)


@pytest.mark.skipif(os.name == "nt", reason="symlink creation is not reliably available on Windows")
def test_symlink_target_bytes_are_compared(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    old = _blob(repo, b"old-target")
    repo.index.entries = {"link": IndexEntry("link", old, "120000")}
    repo.index.save()
    os.symlink("new-target", repo.worktree / "link")
    entries = diff_files(repo)
    assert len(entries) == 1
    assert entries[0].new_mode == "120000"
    assert entries[0].new_oid == BlobObject(b"new-target").hash()


def test_formatters_and_cli_routes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    root, tip, _, new, _ = _history(repo)
    entries = diff_tree(repo, root, tip, patterns=["change.txt"])
    raw = format_diff_entries(entries).decode()
    assert raw.startswith(":100644 100644 ") and raw.endswith(" M\tchange.txt\n")
    assert format_diff_entries(entries, name_status=True) == b"M\tchange.txt\n"
    assert format_diff_entries(entries, name_only=True, nul_terminated=True) == b"change.txt\x00"

    repo.index.entries = {"change.txt": IndexEntry("change.txt", new, "100644")}
    repo.index.save()
    (repo.worktree / "change.txt").write_bytes(b"dirty\n")

    tree = _run(repo, "diff-tree", "--name-status", root, tip, "--", "change.txt")
    assert tree.returncode == 0 and tree.stdout == b"M\tchange.txt\n", tree.stderr.decode()
    cached = _run(repo, "diff-index", "--cached", "--name-only", root, "--", "change.txt")
    assert cached.returncode == 0 and cached.stdout == b"change.txt\n", cached.stderr.decode()
    files = _run(repo, "diff-files", "--exit-code", "--name-only", "--", "change.txt")
    assert files.returncode == 1 and files.stdout == b"change.txt\n"
    quiet = _run(repo, "diff-files", "--quiet", "--", "change.txt")
    assert quiet.returncode == 1 and quiet.stdout == b""
