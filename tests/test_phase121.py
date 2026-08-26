"""Phase 121 tests: rev-list pathname annotations and object edges."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pygit import Repository
from pygit.objects import BlobObject, CommitObject, Identity, TreeEntry, TreeObject
from pygit.rev_list import rev_list_objects
from pygit.rev_list_object_names import (
    rev_list_named_objects,
    rev_list_object_edges,
)


def _commit(
    repo: Repository,
    tree: str,
    parents: list[str],
    message: str,
    timestamp: int,
) -> str:
    identity = Identity("Tester", "tester@example.com", timestamp, "+0000")
    return repo.store.write(
        CommitObject(
            tree=tree,
            parents=parents,
            author=identity,
            committer=identity,
            message=message,
        )
    )


def _history(tmp_path: Path) -> tuple[Repository, dict[str, str]]:
    repo = Repository.init(str(tmp_path / "repo"))

    shared_a = repo.store.write(BlobObject(b"a\n"))
    shared_b = repo.store.write(BlobObject(b"b\n"))
    root_old = repo.store.write(BlobObject(b"root-old\n"))
    root_new = repo.store.write(BlobObject(b"root-new\n"))
    added = repo.store.write(BlobObject(b"added\n"))

    sub_tree = repo.store.write(
        TreeObject([TreeEntry("100644", "b.txt", shared_b)])
    )
    dir_old = repo.store.write(
        TreeObject(
            [
                TreeEntry("100644", "a.txt", shared_a),
                TreeEntry("040000", "sub", sub_tree),
            ]
        )
    )
    tree_old = repo.store.write(
        TreeObject(
            [
                TreeEntry("040000", "dir", dir_old),
                TreeEntry("100644", "root.txt", root_old),
            ]
        )
    )
    base = _commit(repo, tree_old, [], "base", 1)

    dir_new = repo.store.write(
        TreeObject(
            [
                TreeEntry("100644", "a.txt", shared_a),
                TreeEntry("100644", "c.txt", added),
                TreeEntry("040000", "sub", sub_tree),
            ]
        )
    )
    tree_new = repo.store.write(
        TreeObject(
            [
                TreeEntry("040000", "dir", dir_new),
                TreeEntry("100644", "root.txt", root_new),
            ]
        )
    )
    tip = _commit(repo, tree_new, [base], "tip", 2)

    repo.refs.set_branch("base", base)
    repo.refs.set_branch("main", tip)
    repo.refs.set_head_symbolic("main")

    return repo, {
        "shared_a": shared_a,
        "shared_b": shared_b,
        "root_old": root_old,
        "root_new": root_new,
        "added": added,
        "sub_tree": sub_tree,
        "dir_old": dir_old,
        "dir_new": dir_new,
        "tree_old": tree_old,
        "tree_new": tree_new,
        "base": base,
        "tip": tip,
    }


def _run(repo: Repository, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_named_objects_preserve_phase75_set_and_assign_first_paths(tmp_path: Path) -> None:
    repo, h = _history(tmp_path)

    base_entries = rev_list_objects(repo, ["base..main"], topo_order=True)
    named = rev_list_named_objects(repo, ["base..main"], topo_order=True)

    assert {entry.oid for entry in named} == {entry.oid for entry in base_entries}
    assert [(entry.oid, entry.path) for entry in named] == [
        (h["tip"], None),
        (h["tree_new"], ""),
        (h["dir_new"], "dir"),
        (h["added"], "dir/c.txt"),
        (h["root_new"], "root.txt"),
    ]

    # Unchanged objects are still removed by Phase 75's complete negative-side
    # closure before pathname decoration happens.
    assert h["shared_a"] not in {entry.oid for entry in named}
    assert h["shared_b"] not in {entry.oid for entry in named}
    assert h["sub_tree"] not in {entry.oid for entry in named}


def test_cli_objects_uses_git_style_pathname_annotations(tmp_path: Path) -> None:
    repo, h = _history(tmp_path)

    result = _run(repo, "rev-list", "--objects", "--topo-order", "base..main")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stderr == b""
    assert result.stdout.decode().splitlines() == [
        h["tip"],
        f'{h["tree_new"]} ',
        f'{h["dir_new"]} dir',
        f'{h["added"]} dir/c.txt',
        f'{h["root_new"]} root.txt',
    ]


def test_no_object_names_restores_oid_only_stream(tmp_path: Path) -> None:
    repo, _ = _history(tmp_path)
    expected = [
        entry.oid
        for entry in rev_list_named_objects(repo, ["base..main"], topo_order=True)
    ]

    result = _run(
        repo,
        "rev-list",
        "--objects",
        "--no-object-names",
        "--topo-order",
        "base..main",
    )

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout.decode().splitlines() == expected


def test_objects_count_counts_selected_objects_not_only_commits(tmp_path: Path) -> None:
    repo, _ = _history(tmp_path)
    expected = len(rev_list_named_objects(repo, ["base..main"]))

    result = _run(repo, "rev-list", "--objects", "--count", "base..main")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout == f"{expected}\n".encode("ascii")
    assert expected > 1


def test_objects_edge_emits_boundary_before_named_objects(tmp_path: Path) -> None:
    repo, h = _history(tmp_path)

    assert rev_list_object_edges(repo, ["base..main"]) == (h["base"],)

    result = _run(repo, "rev-list", "--objects-edge", "base..main")
    lines = result.stdout.decode().splitlines()

    assert result.returncode == 0, result.stderr.decode()
    assert lines[0] == f'-{h["base"]}'
    assert lines[1] == h["tip"]
    assert f'{h["tree_new"]} ' in lines
    assert f'{h["added"]} dir/c.txt' in lines


def test_objects_edge_boundary_is_computed_before_max_count(tmp_path: Path) -> None:
    repo, h = _history(tmp_path)

    result = _run(
        repo,
        "rev-list",
        "--objects-edge",
        "--no-object-names",
        "-n",
        "1",
        "base..main",
    )

    assert result.returncode == 0, result.stderr.decode()
    lines = result.stdout.decode().splitlines()
    assert lines[0] == f'-{h["base"]}'
    assert h["tip"] in lines[1:]
    assert all(" " not in line for line in lines)


def test_symmetric_range_reports_common_parent_as_edge(tmp_path: Path) -> None:
    repo, h = _history(tmp_path)

    left_blob = repo.store.write(BlobObject(b"left\n"))
    right_blob = repo.store.write(BlobObject(b"right\n"))
    left_tree = repo.store.write(TreeObject([TreeEntry("100644", "left", left_blob)]))
    right_tree = repo.store.write(TreeObject([TreeEntry("100644", "right", right_blob)]))
    left = _commit(repo, left_tree, [h["base"]], "left", 3)
    right = _commit(repo, right_tree, [h["base"]], "right", 4)
    repo.refs.set_branch("left", left)
    repo.refs.set_branch("right", right)

    assert rev_list_object_edges(repo, ["left...right"]) == (h["base"],)

    result = _run(repo, "rev-list", "--objects-edge", "left...right")
    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout.decode().splitlines()[0] == f'-{h["base"]}'


def test_unrelated_or_unexcluded_history_does_not_create_edges(tmp_path: Path) -> None:
    repo, _ = _history(tmp_path)

    assert rev_list_object_edges(repo, ["main"]) == ()


def test_objects_edge_still_rejects_left_right_mixed_protocol(tmp_path: Path) -> None:
    repo, _ = _history(tmp_path)

    result = _run(
        repo,
        "rev-list",
        "--objects-edge",
        "--left-right",
        "base...main",
    )

    assert result.returncode == 2
    assert b"--objects/--objects-edge cannot be combined with --left-right" in result.stderr
