"""Phase 75 tests: rev-list object-closure plumbing."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from pygit import Repository
from pygit.objects import BlobObject, CommitObject, Identity, TreeEntry, TreeObject
from pygit.pack_objects import reachable_objects
from pygit.rev_list import rev_list_objects


def _tree(repo: Repository, entries: list[tuple[str, bytes]]) -> tuple[str, dict[str, str]]:
    blobs: dict[str, str] = {}
    tree_entries = []
    for name, payload in entries:
        oid = repo.store.write(BlobObject(payload))
        blobs[name] = oid
        tree_entries.append(TreeEntry("100644", name, oid))
    tree = repo.store.write(TreeObject(tree_entries))
    return tree, blobs


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


def _graph(tmp_path: Path) -> tuple[Repository, dict[str, str]]:
    repo = Repository.init(str(tmp_path / "repo"))

    shared = repo.store.write(BlobObject(b"shared\n"))
    left_blob = repo.store.write(BlobObject(b"left\n"))
    right_blob = repo.store.write(BlobObject(b"right\n"))
    tip_blob = repo.store.write(BlobObject(b"tip\n"))

    root_tree = repo.store.write(
        TreeObject([TreeEntry("100644", "shared.txt", shared)])
    )
    left_tree = repo.store.write(
        TreeObject(
            [
                TreeEntry("100644", "left.txt", left_blob),
                TreeEntry("100644", "shared.txt", shared),
            ]
        )
    )
    right_tree = repo.store.write(
        TreeObject(
            [
                TreeEntry("100644", "right.txt", right_blob),
                TreeEntry("100644", "shared.txt", shared),
            ]
        )
    )
    merge_tree = repo.store.write(
        TreeObject(
            [
                TreeEntry("100644", "left.txt", left_blob),
                TreeEntry("100644", "right.txt", right_blob),
                TreeEntry("100644", "shared.txt", shared),
            ]
        )
    )
    tip_tree = repo.store.write(
        TreeObject(
            [
                TreeEntry("100644", "left.txt", left_blob),
                TreeEntry("100644", "right.txt", right_blob),
                TreeEntry("100644", "shared.txt", shared),
                TreeEntry("100644", "tip.txt", tip_blob),
            ]
        )
    )

    root = _commit(repo, root_tree, [], "root", 1)
    left = _commit(repo, left_tree, [root], "left", 2)
    right = _commit(repo, right_tree, [root], "right", 3)
    merge = _commit(repo, merge_tree, [left, right], "merge", 4)
    tip = _commit(repo, tip_tree, [merge], "tip", 5)

    repo.refs.set_branch("main", tip)
    repo.refs.set_branch("left", left)
    repo.refs.set_branch("right", right)
    repo.refs.set_head_symbolic("main")
    repo.refs.set_tag("blob-only", shared)

    return repo, {
        "shared": shared,
        "left_blob": left_blob,
        "right_blob": right_blob,
        "tip_blob": tip_blob,
        "root_tree": root_tree,
        "left_tree": left_tree,
        "right_tree": right_tree,
        "merge_tree": merge_tree,
        "tip_tree": tip_tree,
        "root": root,
        "left": left,
        "right": right,
        "merge": merge,
        "tip": tip,
    }


def _oids(entries) -> list[str]:
    return [entry.oid for entry in entries]


def _run(repo: Repository, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_two_dot_objects_subtract_negative_side_complete_closure(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    entries = rev_list_objects(repo, ["left..HEAD"], topo_order=True)
    oids = set(_oids(entries))

    assert {h["tip"], h["merge"], h["right"]}.issubset(oids)
    assert {h["tip_tree"], h["merge_tree"], h["right_tree"]}.issubset(oids)
    assert {h["right_blob"], h["tip_blob"]}.issubset(oids)

    # These objects are still referenced by the selected merge/tip snapshots,
    # but the negative side already owns them.  Object-set subtraction must
    # therefore remove the complete left/root closure, not only their commits.
    assert h["left"] not in oids
    assert h["root"] not in oids
    assert h["left_tree"] not in oids
    assert h["root_tree"] not in oids
    assert h["left_blob"] not in oids
    assert h["shared"] not in oids


def test_max_count_does_not_reintroduce_omitted_parent_ancestry(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    entries = rev_list_objects(repo, ["HEAD"], topo_order=True, max_count=1)
    oids = set(_oids(entries))

    assert h["tip"] in oids
    assert h["tip_tree"] in oids
    assert {h["shared"], h["left_blob"], h["right_blob"], h["tip_blob"]}.issubset(oids)
    assert h["merge"] not in oids
    assert h["merge_tree"] not in oids
    assert h["left"] not in oids
    assert h["right"] not in oids
    assert h["root"] not in oids


def test_symmetric_objects_exclude_complete_common_ancestry(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    entries = rev_list_objects(repo, ["left...right"], topo_order=True)
    oids = set(_oids(entries))

    assert {h["left"], h["right"], h["left_tree"], h["right_tree"]}.issubset(oids)
    assert {h["left_blob"], h["right_blob"]}.issubset(oids)
    assert h["root"] not in oids
    assert h["root_tree"] not in oids
    assert h["shared"] not in oids


def test_object_entries_are_typed_and_commits_keep_rev_list_order(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    entries = rev_list_objects(repo, ["left..HEAD"], topo_order=True)
    assert [(entry.oid, entry.type_name) for entry in entries[:3]] == [
        (h["tip"], "commit"),
        (h["merge"], "commit"),
        (h["right"], "commit"),
    ]
    assert {entry.type_name for entry in entries} <= {"commit", "tree", "blob", "tag"}
    assert all(len(entry.oid) == 64 for entry in entries)


def test_shared_reachability_walker_preserves_pack_objects_default_behavior(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    full = reachable_objects(repo, [h["tip"]])
    selected_only = reachable_objects(repo, [h["tip"]], follow_commit_parents=False)

    assert {h["tip"], h["merge"], h["left"], h["right"], h["root"]}.issubset(full)
    assert h["merge"] not in selected_only
    assert h["root"] not in selected_only
    assert {h["tip"], h["tip_tree"], h["shared"], h["tip_blob"]}.issubset(selected_only)


def test_all_objects_respects_shallow_boundaries_and_ignores_noncommit_refs(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)
    (repo.pygit_dir / "shallow").write_text(h["merge"] + "\n", encoding="utf-8")

    entries = rev_list_objects(repo, ["HEAD"], topo_order=True)
    oids = set(_oids(entries))

    assert {h["tip"], h["merge"], h["tip_tree"], h["merge_tree"]}.issubset(oids)
    assert h["left"] not in oids
    assert h["right"] not in oids
    assert h["root"] not in oids

    all_entries = rev_list_objects(repo, all_refs=True)
    assert all(entry.type_name != "tag" for entry in all_entries)


def test_cli_objects_matches_api_and_rejects_ambiguous_modes(tmp_path: Path) -> None:
    repo, _ = _graph(tmp_path)

    expected = _oids(rev_list_objects(repo, ["left..HEAD"], topo_order=True))
    result = _run(repo, "rev-list", "--objects", "--topo-order", "left..HEAD")
    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout.decode().splitlines() == expected

    counted = _run(repo, "rev-list", "--objects", "--count", "HEAD")
    assert counted.returncode != 0
    assert b"--objects cannot be combined with --count" in counted.stderr

    marked = _run(repo, "rev-list", "--objects", "--left-right", "left...right")
    assert marked.returncode != 0
    assert b"--objects cannot be combined with --left-right" in marked.stderr
