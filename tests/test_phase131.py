"""Phase 131 tests: rev-list symmetric side filters and side-aware counts."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from pygit import Repository
from pygit.objects import BlobObject, CommitObject, Identity, TreeEntry, TreeObject
from pygit.rev_list_sides import count_sides, rev_list_sides


def _commit(repo: Repository, tree: str, parents: list[str], name: str, timestamp: int) -> str:
    ident = Identity("Tester", "tester@example.com", timestamp, "+0000")
    return repo.store.write(
        CommitObject(
            tree=tree,
            parents=parents,
            author=ident,
            committer=ident,
            message=name,
        )
    )


def _graph(tmp_path: Path) -> tuple[Repository, dict[str, str]]:
    repo = Repository.init(str(tmp_path / "repo"))
    blob = repo.store.write(BlobObject(b"side-counts\n"))
    tree = repo.store.write(TreeObject([TreeEntry("100644", "file.txt", blob)]))

    root = _commit(repo, tree, [], "root", 0)
    left1 = _commit(repo, tree, [root], "left-1", 1)
    left2 = _commit(repo, tree, [left1], "left-2", 4)
    right1 = _commit(repo, tree, [root], "right-1", 2)
    right2 = _commit(repo, tree, [right1], "right-2", 3)

    repo.refs.set_branch("left", left2)
    repo.refs.set_branch("right", right2)
    repo.refs.set_branch("main", left2)
    repo.refs.set_head_symbolic("main")
    return repo, {
        "root": root,
        "left1": left1,
        "left2": left2,
        "right1": right1,
        "right2": right2,
    }


def _run(repo: Repository, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _oids(entries) -> list[str]:
    return [entry.oid for entry in entries]


def test_side_filters_preserve_each_side_order(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    left = rev_list_sides(repo, ["left...right"], topo_order=True, left_only=True)
    right = rev_list_sides(repo, ["left...right"], topo_order=True, right_only=True)

    assert _oids(left) == [h["left2"], h["left1"]]
    assert _oids(right) == [h["right2"], h["right1"]]
    assert all(entry.side == "<" for entry in left)
    assert all(entry.side == ">" for entry in right)


def test_side_filter_runs_before_skip_limit_and_reverse(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    limited = rev_list_sides(
        repo,
        ["left...right"],
        topo_order=True,
        left_only=True,
        skip=1,
        max_count=1,
    )
    reversed_left = rev_list_sides(
        repo,
        ["left...right"],
        topo_order=True,
        left_only=True,
        max_count=2,
        reverse=True,
    )

    assert _oids(limited) == [h["left1"]]
    assert _oids(reversed_left) == [h["left1"], h["left2"]]


def test_side_count_helper_counts_selected_markers(tmp_path: Path) -> None:
    repo, _ = _graph(tmp_path)

    all_entries = rev_list_sides(repo, ["left...right"], topo_order=True)
    left_entries = rev_list_sides(repo, ["left...right"], left_only=True)
    right_entries = rev_list_sides(repo, ["left...right"], right_only=True)

    assert count_sides(all_entries) == (2, 2)
    assert count_sides(left_entries) == (2, 0)
    assert count_sides(right_entries) == (0, 2)


def test_helper_rejects_conflicting_side_filters(tmp_path: Path) -> None:
    repo, _ = _graph(tmp_path)

    with pytest.raises(ValueError, match="cannot be used together"):
        rev_list_sides(
            repo,
            ["left...right"],
            left_only=True,
            right_only=True,
        )


def test_cli_left_right_count_uses_two_columns(tmp_path: Path) -> None:
    repo, _ = _graph(tmp_path)

    result = _run(repo, "rev-list", "--left-right", "--count", "left...right")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stderr == b""
    assert result.stdout == b"2\t2\n"


def test_cli_side_filters_compose_with_markers_and_counts(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    left_plain = _run(repo, "rev-list", "--topo-order", "--left-only", "left...right")
    left_marked = _run(
        repo,
        "rev-list",
        "--topo-order",
        "--left-right",
        "--left-only",
        "left...right",
    )
    left_count = _run(
        repo,
        "rev-list",
        "--left-right",
        "--left-only",
        "--count",
        "left...right",
    )
    right_count = _run(repo, "rev-list", "--right-only", "--count", "left...right")

    assert left_plain.returncode == 0, left_plain.stderr.decode()
    assert left_plain.stdout.decode().splitlines() == [h["left2"], h["left1"]]
    assert left_marked.returncode == 0, left_marked.stderr.decode()
    assert left_marked.stdout.decode().splitlines() == [f"<{h['left2']}", f"<{h['left1']}"]
    assert left_count.returncode == 0, left_count.stderr.decode()
    assert left_count.stdout == b"2\t0\n"
    assert right_count.returncode == 0, right_count.stderr.decode()
    assert right_count.stdout == b"2\n"


def test_cli_side_filter_precedes_skip_for_count(tmp_path: Path) -> None:
    repo, _ = _graph(tmp_path)

    result = _run(
        repo,
        "rev-list",
        "--left-right",
        "--left-only",
        "--skip",
        "1",
        "--count",
        "left...right",
    )

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout == b"1\t0\n"


def test_cli_rejects_conflicting_filters_and_object_mode(tmp_path: Path) -> None:
    repo, _ = _graph(tmp_path)

    both = _run(repo, "rev-list", "--left-only", "--right-only", "left...right")
    objects = _run(repo, "rev-list", "--objects", "--left-only", "left...right")

    assert both.returncode == 2
    assert b"not allowed with argument" in both.stderr
    assert objects.returncode == 2
    assert b"cannot be combined" in objects.stderr


def test_installed_help_lists_phase131_side_options(tmp_path: Path) -> None:
    repo, _ = _graph(tmp_path)

    result = _run(repo, "rev-list", "--help")

    assert result.returncode == 0
    assert b"--left-right" in result.stdout
    assert b"--left-only" in result.stdout
    assert b"--right-only" in result.stdout
