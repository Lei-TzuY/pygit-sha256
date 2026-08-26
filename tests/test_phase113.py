"""Phase 113 tests: explicit commit-graph reachable-coverage verification."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from pygit import Repository
from pygit.commit_graph import CommitGraph, CommitGraphError
from pygit.commit_graph_reachability import (
    verify_commit_graph_coverage,
    write_reachable_commit_graph,
)
from pygit.objects import CommitObject
from pygit.objects.commit import Identity


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def _commit_file(repo: Repository, content: str, message: str) -> str:
    path = repo.worktree / "tracked.txt"
    path.write_text(content, encoding="utf-8")
    repo.add(["tracked.txt"])
    return repo.commit(message, author_name="Tester", author_email="tester@example.com")


def _manual_commit(repo: Repository, tree: str, message: str, parents=()) -> str:
    identity = Identity("Coverage Tester", "coverage@example.com", timestamp=1_700_000_000)
    return repo.store.write(
        CommitObject(
            tree=tree,
            parents=list(parents),
            author=identity,
            committer=identity,
            message=message,
        )
    )


def _run(
    repo: Repository,
    *args: str,
    input_text: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        input=input_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def test_default_verify_still_accepts_stale_but_valid_graph(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first = _commit_file(repo, "one\n", "one")
    write_reachable_commit_graph(repo)
    second = _commit_file(repo, "two\n", "two")

    graph = CommitGraph(repo.pygit_dir)
    result = graph.verify(repo.store)
    assert result.commit_count == 1
    assert set(graph.entries) == {first}
    assert second not in graph.entries

    default_cli = _run(repo, "commit-graph", "verify")
    assert default_cli.returncode == 0, default_cli.stderr
    assert "1 commits" in default_cli.stdout

    with pytest.raises(CommitGraphError, match="missing 1 reachable commit"):
        verify_commit_graph_coverage(repo)

    reachable_cli = _run(repo, "commit-graph", "verify", "--reachable")
    assert reachable_cli.returncode == 1
    assert reachable_cli.stdout == ""
    assert "missing 1 reachable commit" in reachable_cli.stderr


def test_reachable_coverage_allows_extra_unreachable_entries(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    tip = _commit_file(repo, "main\n", "main")
    tip_obj = repo.store.read(tip)
    assert isinstance(tip_obj, CommitObject)
    orphan = _manual_commit(repo, tip_obj.tree, "orphan")

    write_reachable_commit_graph(repo, [tip, orphan])
    coverage = verify_commit_graph_coverage(repo)

    assert coverage.expected_count == 1
    assert coverage.indexed_count == 2
    assert coverage.extra_count == 1


def test_stdin_coverage_can_verify_deliberate_subset(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    main_tip = _commit_file(repo, "main\n", "main")
    main_obj = repo.store.read(main_tip)
    assert isinstance(main_obj, CommitObject)
    remote_tip = _manual_commit(repo, main_obj.tree, "remote-only")
    repo.refs.set_remote("origin", "topic", remote_tip)

    write_reachable_commit_graph(repo, [main_tip])

    repo_wide = _run(repo, "commit-graph", "verify", "--reachable")
    assert repo_wide.returncode == 1
    assert "missing 1 reachable commit" in repo_wide.stderr

    subset = _run(
        repo,
        "commit-graph",
        "verify",
        "--reachable",
        "--stdin-commits",
        input_text=f"{main_tip}\n\n",
    )
    assert subset.returncode == 0, subset.stderr
    assert "1 reachable" in subset.stdout
    assert "1 indexed" in subset.stdout
    assert "0 extra" in subset.stdout


def test_stdin_coverage_respects_shallow_boundary(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first = _commit_file(repo, "base\n", "base")
    first_obj = repo.store.read(first)
    assert isinstance(first_obj, CommitObject)
    second = _manual_commit(repo, first_obj.tree, "second", parents=[first])
    third = _manual_commit(repo, first_obj.tree, "third", parents=[second])
    (repo.pygit_dir / "shallow").write_text(f"{second}\n", encoding="utf-8")

    write_reachable_commit_graph(repo, [third])
    coverage = verify_commit_graph_coverage(repo, [third])

    assert coverage.expected_count == 2
    assert coverage.indexed_count == 2
    assert coverage.extra_count == 0


def test_verify_stdin_requires_reachable_and_nonempty_input(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _commit_file(repo, "main\n", "main")
    write_reachable_commit_graph(repo)

    meaningless = _run(repo, "commit-graph", "verify", "--stdin-commits")
    assert meaningless.returncode == 2
    assert "--stdin-commits requires --reachable" in meaningless.stderr

    empty = _run(
        repo,
        "commit-graph",
        "verify",
        "--reachable",
        "--stdin-commits",
    )
    assert empty.returncode == 1
    assert "received no commits" in empty.stderr


def test_verify_help_exposes_coverage_options(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    help_result = _run(repo, "commit-graph", "verify", "--help")

    assert help_result.returncode == 0, help_result.stderr
    assert "--reachable" in help_result.stdout
    assert "--stdin-commits" in help_result.stdout
