"""Phase 110 tests: commit-graph generation without recursion depth limits."""

from __future__ import annotations

from pathlib import Path

import pytest

from pygit.commit_graph import CommitGraph, CommitGraphError


_TREE = "f" * 64
_EXTERNAL = "e" * 64


def _oid(index: int) -> str:
    return f"{index:064x}"


def _linear_history(count: int):
    commits = []
    previous = None
    for index in range(1, count + 1):
        sha = _oid(index)
        parents = [previous] if previous is not None else []
        commits.append((sha, _TREE, parents))
        previous = sha
    return commits


def test_deep_linear_history_serializes_and_parses_without_recursion() -> None:
    commits = _linear_history(5000)

    data = CommitGraph._serialize(commits)
    parsed = CommitGraph._parse_bytes(data)

    assert len(parsed) == 5000
    assert parsed[_oid(1)][2] == 1
    assert parsed[_oid(2500)][2] == 2500
    assert parsed[_oid(5000)][2] == 5000


def test_public_write_and_read_handles_deep_history(tmp_path: Path) -> None:
    graph = CommitGraph(tmp_path / ".pygit")
    commits = _linear_history(3000)

    path = graph.write(commits)
    parsed = graph.read()

    assert path == tmp_path / ".pygit" / "objects" / "info" / "commit-graph"
    assert parsed[_oid(3000)][2] == 3000
    assert len(parsed) == 3000


def test_deep_cycle_reports_commit_graph_error_not_recursion_error() -> None:
    commits = _linear_history(3000)
    first_sha, first_tree, _ = commits[0]
    commits[0] = (first_sha, first_tree, [_oid(3000)])

    with pytest.raises(CommitGraphError, match="commit-graph cycle detected"):
        CommitGraph._serialize(commits)


def test_external_parent_generation_semantics_are_preserved() -> None:
    root = _oid(1)
    shallow_child = _oid(2)
    merge = _oid(3)
    tip = _oid(4)
    commit_map = {
        root: (_TREE, []),
        shallow_child: (_TREE, [_EXTERNAL]),
        merge: (_TREE, [root, shallow_child]),
        tip: (_TREE, [merge, root]),
    }

    generations = CommitGraph._compute_generations(commit_map)

    assert generations[root] == 1
    assert generations[shallow_child] == 2
    assert generations[merge] == 3
    assert generations[tip] == 4


def test_duplicate_parent_edges_do_not_stall_iterative_traversal() -> None:
    root = _oid(1)
    child = _oid(2)
    commit_map = {
        root: (_TREE, []),
        child: (_TREE, [root, root]),
    }

    generations = CommitGraph._compute_generations(commit_map)

    assert generations == {root: 1, child: 2}
