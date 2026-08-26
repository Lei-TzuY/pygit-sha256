"""Phase 132 tests: two-tree read-tree carry-forward merges."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from pygit import Repository
from pygit.index_plumbing import update_index
from pygit.objects import BlobObject, TreeEntry, TreeObject
from pygit.read_tree_merge import read_tree_three_way, read_tree_two_way


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def _blob(repo: Repository, data: bytes) -> str:
    return repo.store.write(BlobObject(data))


def _tree(repo: Repository, files: dict[str, bytes]) -> tuple[str, dict[str, str]]:
    oids: dict[str, str] = {}
    entries = []
    for name, data in sorted(files.items()):
        oid = _blob(repo, data)
        oids[name] = oid
        entries.append(TreeEntry("100644", name, oid))
    return repo.store.write(TreeObject(entries)), oids


def _run(repo: Repository, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_two_way_clean_fast_forward_updates_adds_and_deletes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    head, head_oids = _tree(repo, {"change.txt": b"old\n", "delete.txt": b"gone\n"})
    merge, merge_oids = _tree(repo, {"change.txt": b"new\n", "add.txt": b"added\n"})
    update_index(
        repo,
        cache_info=[
            ("100644", head_oids["change.txt"], "change.txt"),
            ("100644", head_oids["delete.txt"], "delete.txt"),
        ],
    )

    read_tree_two_way(repo, head, merge)

    assert repo.index.get("change.txt").sha == merge_oids["change.txt"]
    assert repo.index.get("add.txt").sha == merge_oids["add.txt"]
    assert repo.index.get("delete.txt") is None
    assert not repo.index.has_unmerged()


def test_two_way_keeps_local_stage_when_trees_agree(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    head, tree_oids = _tree(repo, {"file.txt": b"tree\n"})
    local = _blob(repo, b"local staged\n")
    update_index(repo, cache_info=[("100755", local, "file.txt")])
    before = repo.index.get("file.txt")

    read_tree_two_way(repo, head, head)

    after = repo.index.get("file.txt")
    assert after.sha == local
    assert after.mode == "100755"
    assert after.size == before.size
    assert tree_oids["file.txt"] != local


def test_two_way_keeps_local_addition_and_staged_deletion(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    head, head_oids = _tree(repo, {"deleted.txt": b"same\n"})
    merge, _ = _tree(repo, {"deleted.txt": b"same\n"})
    local = _blob(repo, b"local add\n")
    update_index(repo, cache_info=[("100644", local, "local.txt")])

    read_tree_two_way(repo, head, merge)

    assert repo.index.get("deleted.txt") is None
    assert repo.index.get("local.txt").sha == local
    assert head_oids["deleted.txt"] not in [entry.sha for entry in repo.index.all_entries()]


def test_two_way_accepts_index_already_equal_to_destination(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    head, _ = _tree(repo, {"file.txt": b"old\n"})
    merge, merge_oids = _tree(repo, {"file.txt": b"new\n"})
    update_index(repo, cache_info=[("100644", merge_oids["file.txt"], "file.txt")])

    read_tree_two_way(repo, head, merge)

    assert repo.index.get("file.txt").sha == merge_oids["file.txt"]


def test_two_way_conflicting_staged_change_fails_atomically(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    head, _ = _tree(repo, {"file.txt": b"old\n"})
    merge, _ = _tree(repo, {"file.txt": b"upstream\n"})
    local = _blob(repo, b"local staged\n")
    keep = _blob(repo, b"keep\n")
    update_index(
        repo,
        cache_info=[
            ("100644", local, "file.txt"),
            ("100644", keep, "keep.txt"),
        ],
    )

    with pytest.raises(RuntimeError, match="overwrite staged changes"):
        read_tree_two_way(repo, head, merge)

    assert repo.index.get("file.txt").sha == local
    assert repo.index.get("keep.txt").sha == keep


def test_two_way_empty_index_initial_checkout_populates_equal_trees(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    head, oids = _tree(repo, {"file.txt": b"same\n"})

    read_tree_two_way(repo, head, head)

    assert repo.index.get("file.txt").sha == oids["file.txt"]


def test_two_way_rejects_existing_unmerged_index_without_mutation(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    head, _ = _tree(repo, {"file.txt": b"old\n"})
    merge, _ = _tree(repo, {"file.txt": b"new\n"})
    base, _ = _tree(repo, {"conflict.txt": b"base\n"})
    ours, _ = _tree(repo, {"conflict.txt": b"ours\n"})
    theirs, _ = _tree(repo, {"conflict.txt": b"theirs\n"})
    read_tree_three_way(repo, base, ours, theirs)
    before = [(e.path, e.stage, e.sha) for e in repo.index.all_entries(include_unmerged=True)]

    with pytest.raises(RuntimeError, match="unmerged index entries"):
        read_tree_two_way(repo, head, merge)

    assert [(e.path, e.stage, e.sha) for e in repo.index.all_entries(include_unmerged=True)] == before


def test_installed_two_way_merge_is_index_only_and_cli_validates_shape(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    target = repo.worktree / "file.txt"
    target.write_bytes(b"worktree stays\n")
    head, head_oids = _tree(repo, {"file.txt": b"old\n"})
    merge, merge_oids = _tree(repo, {"file.txt": b"new\n"})
    update_index(repo, cache_info=[("100644", head_oids["file.txt"], "file.txt")])

    result = _run(repo, "read-tree", "-m", head, merge)
    assert result.returncode == 0, result.stderr
    reopened = Repository(str(repo.worktree))
    assert reopened.index.get("file.txt").sha == merge_oids["file.txt"]
    assert target.read_bytes() == b"worktree stays\n"

    aggressive = _run(reopened, "read-tree", "-m", "--aggressive", head, merge)
    assert aggressive.returncode == 2
    one_tree = _run(reopened, "read-tree", "-m", head)
    assert one_tree.returncode == 2
