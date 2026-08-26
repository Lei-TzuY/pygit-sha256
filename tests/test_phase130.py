"""Phase 130 tests: three-way read-tree merge and unmerged inspection."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from pygit import Repository
from pygit.index_plumbing import update_index
from pygit.objects import BlobObject, TreeEntry, TreeObject
from pygit.read_tree_merge import read_tree_three_way


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


def _nested_tree(repo: Repository, name: str, child: str, data: bytes) -> str:
    oid = _blob(repo, data)
    subtree = repo.store.write(TreeObject([TreeEntry("100644", child, oid)]))
    return repo.store.write(TreeObject([TreeEntry("040000", name, subtree)]))


def _run(repo: Repository, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_three_way_conflict_persists_base_ours_theirs_stages(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    base, base_oids = _tree(repo, {"file.txt": b"base\n"})
    ours, ours_oids = _tree(repo, {"file.txt": b"ours\n"})
    theirs, theirs_oids = _tree(repo, {"file.txt": b"theirs\n"})

    read_tree_three_way(repo, base, ours, theirs)

    assert repo.index.get("file.txt") is None
    assert repo.index.get("file.txt", 1).sha == base_oids["file.txt"]
    assert repo.index.get("file.txt", 2).sha == ours_oids["file.txt"]
    assert repo.index.get("file.txt", 3).sha == theirs_oids["file.txt"]

    reopened = Repository(str(repo.worktree))
    assert [entry.stage for entry in reopened.index.stage_entries("file.txt")] == [1, 2, 3]


def test_three_way_trivial_rules_resolve_to_stage_zero(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    base, _ = _tree(
        repo,
        {
            "same.txt": b"base-same\n",
            "ours-unchanged.txt": b"base-ours\n",
            "theirs-unchanged.txt": b"base-theirs\n",
        },
    )
    ours, ours_oids = _tree(
        repo,
        {
            "same.txt": b"both-new\n",
            "ours-unchanged.txt": b"base-ours\n",
            "theirs-unchanged.txt": b"ours-new\n",
            "ours-add.txt": b"ours-add\n",
        },
    )
    theirs, theirs_oids = _tree(
        repo,
        {
            "same.txt": b"both-new\n",
            "ours-unchanged.txt": b"theirs-new\n",
            "theirs-unchanged.txt": b"base-theirs\n",
        },
    )

    read_tree_three_way(repo, base, ours, theirs)

    assert not repo.index.has_unmerged()
    assert repo.index.get("same.txt").sha == ours_oids["same.txt"] == theirs_oids["same.txt"]
    assert repo.index.get("ours-unchanged.txt").sha == theirs_oids["ours-unchanged.txt"]
    assert repo.index.get("theirs-unchanged.txt").sha == ours_oids["theirs-unchanged.txt"]
    assert repo.index.get("ours-add.txt").sha == ours_oids["ours-add.txt"]


def test_default_merge_preserves_native_deletion_conflict_stages(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    base, base_oids = _tree(
        repo,
        {
            "both-delete.txt": b"both\n",
            "theirs-delete.txt": b"td\n",
            "ours-delete.txt": b"od\n",
        },
    )
    ours, ours_oids = _tree(repo, {"theirs-delete.txt": b"td\n"})
    theirs, theirs_oids = _tree(repo, {"ours-delete.txt": b"od\n"})

    read_tree_three_way(repo, base, ours, theirs)

    assert [(e.stage, e.sha) for e in repo.index.stage_entries("both-delete.txt")] == [
        (1, base_oids["both-delete.txt"])
    ]
    assert [(e.stage, e.sha) for e in repo.index.stage_entries("theirs-delete.txt")] == [
        (1, base_oids["theirs-delete.txt"]),
        (2, ours_oids["theirs-delete.txt"]),
    ]
    assert [(e.stage, e.sha) for e in repo.index.stage_entries("ours-delete.txt")] == [
        (1, base_oids["ours-delete.txt"]),
        (3, theirs_oids["ours-delete.txt"]),
    ]


def test_aggressive_merge_resolves_trivial_deletions(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    base, _ = _tree(
        repo,
        {
            "both-delete.txt": b"both\n",
            "theirs-delete.txt": b"td\n",
            "ours-delete.txt": b"od\n",
        },
    )
    ours, _ = _tree(repo, {"theirs-delete.txt": b"td\n"})
    theirs, _ = _tree(repo, {"ours-delete.txt": b"od\n"})

    read_tree_three_way(repo, base, ours, theirs, aggressive=True)

    assert repo.index.all_entries(include_unmerged=True) == []


def test_merge_is_index_only_and_does_not_touch_worktree(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    target = repo.worktree / "file.txt"
    target.write_bytes(b"local worktree bytes\n")
    base, _ = _tree(repo, {"file.txt": b"base\n"})
    ours, _ = _tree(repo, {"file.txt": b"ours\n"})
    theirs, _ = _tree(repo, {"file.txt": b"theirs\n"})

    read_tree_three_way(repo, base, ours, theirs)

    assert target.read_bytes() == b"local worktree bytes\n"


def test_directory_file_conflict_rejection_is_atomic(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    sentinel = _blob(repo, b"sentinel\n")
    update_index(repo, cache_info=[("100644", sentinel, "keep.txt")])

    file_tree, _ = _tree(repo, {"dir": b"file\n"})
    nested = _nested_tree(repo, "dir", "child.txt", b"nested\n")

    with pytest.raises(RuntimeError, match="directory/file conflicts"):
        read_tree_three_way(repo, file_tree, nested, nested)

    assert repo.index.get("keep.txt").sha == sentinel
    assert not repo.index.has_unmerged()


def test_installed_read_tree_merge_and_ls_files_unmerged(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    base, base_oids = _tree(repo, {"file.txt": b"base\n"})
    ours, ours_oids = _tree(repo, {"file.txt": b"ours\n"})
    theirs, theirs_oids = _tree(repo, {"file.txt": b"theirs\n"})

    merged = _run(repo, "read-tree", "-m", base, ours, theirs)
    assert merged.returncode == 0, merged.stderr
    assert merged.stdout == ""

    unmerged = _run(repo, "ls-files", "-u")
    assert unmerged.returncode == 0, unmerged.stderr
    assert unmerged.stdout == (
        f"100644 {base_oids['file.txt']} 1\tfile.txt\n"
        f"100644 {ours_oids['file.txt']} 2\tfile.txt\n"
        f"100644 {theirs_oids['file.txt']} 3\tfile.txt\n"
    )

    nul = subprocess.run(
        [sys.executable, "-m", "pygit", "ls-files", "-u", "-z"],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert nul.returncode == 0, nul.stderr.decode("utf-8", "replace")
    assert nul.stdout.count(b"\x00") == 3
    assert nul.stdout.endswith(b"\x00")


def test_installed_replacing_read_tree_and_empty_clear_old_conflict_stages(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    base, _ = _tree(repo, {"conflict.txt": b"base\n"})
    ours, _ = _tree(repo, {"conflict.txt": b"ours\n"})
    theirs, _ = _tree(repo, {"conflict.txt": b"theirs\n"})
    replacement, replacement_oids = _tree(repo, {"clean.txt": b"clean\n"})
    read_tree_three_way(repo, base, ours, theirs)
    assert repo.index.has_unmerged()

    replaced = _run(repo, "read-tree", replacement)
    assert replaced.returncode == 0, replaced.stderr
    reopened = Repository(str(repo.worktree))
    assert not reopened.index.has_unmerged()
    assert reopened.index.get("clean.txt").sha == replacement_oids["clean.txt"]

    read_tree_three_way(reopened, base, ours, theirs)
    emptied = _run(reopened, "read-tree", "--empty")
    assert emptied.returncode == 0, emptied.stderr
    final = Repository(str(repo.worktree))
    assert final.index.all_entries(include_unmerged=True) == []


def test_invalid_merge_cli_shapes_fail_without_mutating_index(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    sentinel = _blob(repo, b"sentinel\n")
    update_index(repo, cache_info=[("100644", sentinel, "keep.txt")])
    tree, _ = _tree(repo, {"file.txt": b"value\n"})

    too_few = _run(repo, "read-tree", "-m", tree, tree)
    assert too_few.returncode == 2

    update = _run(repo, "read-tree", "-m", "-u", tree, tree, tree)
    assert update.returncode == 2

    aggressive = _run(repo, "read-tree", "--aggressive", tree)
    assert aggressive.returncode == 2

    reopened = Repository(str(repo.worktree))
    assert reopened.index.get("keep.txt").sha == sentinel
