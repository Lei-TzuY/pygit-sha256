"""Phase 113 tests: optional commit-graph reachability coverage verification."""

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


def _manual_commit(
    repo: Repository,
    tree: str,
    message: str,
    parents=(),
) -> str:
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


def _repo_with_uncovered_remote(tmp_path: Path):
    repo = _repo(tmp_path)
    main_tip = _commit_file(repo, "main\n", "main")
    main_obj = repo.store.read(main_tip)
    assert isinstance(main_obj, CommitObject)
    remote_tip = _manual_commit(repo, main_obj.tree, "remote-only")
    repo.refs.set_remote("origin", "topic", remote_tip)
    write_reachable_commit_graph(repo, [main_tip])
    return repo, main_tip, remote_tip


def test_default_verify_remains_structural_while_reachable_detects_missing_root(
    tmp_path: Path,
) -> None:
    repo, main_tip, remote_tip = _repo_with_uncovered_remote(tmp_path)
    graph = CommitGraph(repo.pygit_dir)
    before = graph.graph_file.read_bytes()

    # Phase 103 verification deliberately remains valid for intentional subsets.
    assert graph.verify(repo.store).commit_count == 1
    assert set(graph.read()) == {main_tip}

    with pytest.raises(CommitGraphError, match="missing 1 reachable commit") as excinfo:
        verify_commit_graph_coverage(repo)
    assert remote_tip in str(excinfo.value)
    assert graph.graph_file.read_bytes() == before

    ordinary = _run(repo, "commit-graph", "verify")
    assert ordinary.returncode == 0, ordinary.stderr
    assert "coverage ok" not in ordinary.stdout

    covered = _run(repo, "commit-graph", "verify", "--reachable")
    assert covered.returncode == 1
    assert "missing 1 reachable commit" in covered.stderr
    assert remote_tip in covered.stderr
    assert graph.graph_file.read_bytes() == before


def test_repository_wide_coverage_passes_after_normal_write(tmp_path: Path) -> None:
    repo, _main_tip, _remote_tip = _repo_with_uncovered_remote(tmp_path)
    write_reachable_commit_graph(repo)

    result = verify_commit_graph_coverage(repo)
    assert result.expected_count == 2
    assert result.indexed_count == 2
    assert result.extra_count == 0

    cli = _run(repo, "commit-graph", "verify", "--reachable")
    assert cli.returncode == 0, cli.stderr
    assert "coverage ok (2 reachable commits, 0 extra graph commits)" in cli.stdout


def test_explicit_coverage_allows_safe_extra_graph_entries(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    main_tip = _commit_file(repo, "main\n", "main")
    main_obj = repo.store.read(main_tip)
    assert isinstance(main_obj, CommitObject)
    other_tip = _manual_commit(repo, main_obj.tree, "other")
    repo.refs.set_remote("origin", "other", other_tip)
    write_reachable_commit_graph(repo)

    result = verify_commit_graph_coverage(repo, [main_tip])
    assert result.expected_count == 1
    assert result.indexed_count == 2
    assert result.extra_count == 1

    cli = _run(
        repo,
        "commit-graph",
        "verify",
        "--stdin-commits",
        input_text=f"{main_tip}\n",
    )
    assert cli.returncode == 0, cli.stderr
    assert "coverage ok (1 reachable commits, 1 extra graph commits)" in cli.stdout


def test_explicit_stdin_coverage_detects_unindexed_root_and_blank_input(
    tmp_path: Path,
) -> None:
    repo, main_tip, remote_tip = _repo_with_uncovered_remote(tmp_path)

    good = _run(
        repo,
        "commit-graph",
        "verify",
        "--stdin-commits",
        input_text=f"{main_tip}\n",
    )
    assert good.returncode == 0, good.stderr

    missing = _run(
        repo,
        "commit-graph",
        "verify",
        "--stdin-commits",
        input_text=f"{remote_tip}\n",
    )
    assert missing.returncode == 1
    assert "missing 1 reachable commit" in missing.stderr

    blank = _run(repo, "commit-graph", "verify", "--stdin-commits", input_text="\n\n")
    assert blank.returncode == 1
    assert "verify --stdin-commits received no commits" in blank.stderr


def test_explicit_coverage_respects_shallow_boundaries(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first = _commit_file(repo, "base\n", "base")
    first_obj = repo.store.read(first)
    assert isinstance(first_obj, CommitObject)
    second = _manual_commit(repo, first_obj.tree, "second", parents=[first])
    third = _manual_commit(repo, first_obj.tree, "third", parents=[second])
    (repo.pygit_dir / "shallow").write_text(f"{second}\n", encoding="utf-8")

    write_reachable_commit_graph(repo, [third])
    result = verify_commit_graph_coverage(repo, [third])
    assert result.expected_count == 2
    assert result.indexed_count == 2
    assert result.extra_count == 0
    assert first not in CommitGraph(repo.pygit_dir).read()


def test_empty_repository_and_help_are_supported(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    write_reachable_commit_graph(repo)

    coverage = _run(repo, "commit-graph", "verify", "--reachable")
    assert coverage.returncode == 0, coverage.stderr
    assert "coverage ok (0 reachable commits, 0 extra graph commits)" in coverage.stdout

    help_result = _run(repo, "commit-graph", "verify", "--help")
    assert help_result.returncode == 0, help_result.stderr
    assert "--reachable" in help_result.stdout
    assert "--stdin-commits" in help_result.stdout
