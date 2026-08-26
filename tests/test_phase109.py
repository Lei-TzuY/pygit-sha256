"""Phase 109 tests: complete commit-graph root and reachability selection."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from pygit import Repository
from pygit.commit_graph import CommitGraph
from pygit.commit_graph_reachability import (
    collect_commit_graph_commits,
    write_reachable_commit_graph,
)
from pygit.objects import BlobObject, CommitObject, TagObject
from pygit.objects.commit import Identity
from pygit.packed_refs import pack_refs


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
    identity = Identity("Graph Tester", "graph@example.com", timestamp=1_700_000_000)
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


def test_default_selection_covers_packed_refs_remote_tag_and_detached_head(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    branch_tip = _commit_file(repo, "main\n", "main root")
    branch_obj = repo.store.read(branch_tip)
    assert isinstance(branch_obj, CommitObject)

    remote_tip = _manual_commit(repo, branch_obj.tree, "remote-only root")
    tagged_tip = _manual_commit(repo, branch_obj.tree, "tag-only root")
    detached_tip = _manual_commit(repo, branch_obj.tree, "detached-only root")

    repo.refs.set_remote("origin", "topic", remote_tip)
    tag_object = TagObject(
        target_sha=tagged_tip,
        target_type=b"commit",
        tag_name="archive",
        tagger=Identity("Tagger", "tagger@example.com", timestamp=1_700_000_001),
        message="archived history",
    )
    tag_oid = repo.store.write(tag_object)
    repo.refs.set_tag("archive", tag_oid)

    # Prove root discovery is not limited to loose refs. Pack branches, remotes,
    # and tags, then detach HEAD at an otherwise unreferenced commit.
    pack_refs(repo, all_refs=True, prune=True)
    repo.refs.set_head_detached(detached_tip, message="test detached graph root")

    selected = {oid for oid, _tree, _parents in collect_commit_graph_commits(repo)}
    assert {branch_tip, remote_tip, tagged_tip, detached_tip} <= selected
    assert tag_oid not in selected

    path = write_reachable_commit_graph(repo)
    graph = CommitGraph(repo.pygit_dir)
    verified = graph.verify(repo.store)
    assert verified.commit_count == 4
    assert set(graph.read()) == {branch_tip, remote_tip, tagged_tip, detached_tip}


def test_explicit_roots_respect_shallow_boundary(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first = _commit_file(repo, "base\n", "base")
    first_obj = repo.store.read(first)
    assert isinstance(first_obj, CommitObject)
    second = _manual_commit(repo, first_obj.tree, "second", parents=[first])
    third = _manual_commit(repo, first_obj.tree, "third", parents=[second])
    (repo.pygit_dir / "shallow").write_text(f"{second}\n", encoding="utf-8")

    selected = collect_commit_graph_commits(repo, [third])
    assert {oid for oid, _tree, _parents in selected} == {second, third}

    path = write_reachable_commit_graph(repo, [third])
    assert path.is_file()
    entries = CommitGraph(repo.pygit_dir).read()
    assert set(entries) == {second, third}
    assert first not in entries


def test_explicit_roots_do_not_pull_other_repository_refs(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    main_tip = _commit_file(repo, "main\n", "main")
    main_obj = repo.store.read(main_tip)
    assert isinstance(main_obj, CommitObject)
    other_tip = _manual_commit(repo, main_obj.tree, "other root")
    repo.refs.set_remote("origin", "other", other_tip)

    write_reachable_commit_graph(repo, [main_tip])
    entries = CommitGraph(repo.pygit_dir).read()
    assert set(entries) == {main_tip}
    assert other_tip not in entries


def test_empty_repository_still_writes_a_valid_empty_graph(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    path = write_reachable_commit_graph(repo)

    assert path.is_file()
    result = CommitGraph(repo.pygit_dir).verify(repo.store)
    assert result.commit_count == 0
    assert result.max_generation == 0


def test_invalid_explicit_root_does_not_replace_existing_graph(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _commit_file(repo, "stable\n", "stable")
    path = write_reachable_commit_graph(repo)
    before = path.read_bytes()

    with pytest.raises((KeyError, ValueError), match="missing-root|Unknown revision|not found"):
        write_reachable_commit_graph(repo, ["missing-root"])

    assert path.read_bytes() == before
    CommitGraph(repo.pygit_dir).verify(repo.store)


def test_non_commit_explicit_root_fails_before_mutation(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _commit_file(repo, "stable\n", "stable")
    path = write_reachable_commit_graph(repo)
    before = path.read_bytes()
    blob_oid = repo.store.write(BlobObject(b"not-a-commit\n"))

    with pytest.raises((KeyError, ValueError, RuntimeError)):
        write_reachable_commit_graph(repo, [blob_oid])

    assert path.read_bytes() == before


def test_cli_stdin_commits_selects_subset_and_preserves_graph_on_bad_input(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    main_tip = _commit_file(repo, "main\n", "main")
    main_obj = repo.store.read(main_tip)
    assert isinstance(main_obj, CommitObject)
    other_tip = _manual_commit(repo, main_obj.tree, "other")
    repo.refs.set_remote("origin", "other", other_tip)

    written = _run(
        repo,
        "commit-graph",
        "write",
        "--stdin-commits",
        input_text=f"{main_tip}\n\n",
    )
    assert written.returncode == 0, written.stderr
    assert "Wrote commit-graph" in written.stdout
    graph = CommitGraph(repo.pygit_dir)
    assert set(graph.read()) == {main_tip}
    before = graph.graph_file.read_bytes()

    failed = _run(
        repo,
        "commit-graph",
        "write",
        "--stdin-commits",
        input_text="missing-root\n",
    )
    assert failed.returncode == 1
    assert failed.stdout == ""
    assert "missing-root" in failed.stderr
    assert graph.graph_file.read_bytes() == before

    empty = _run(repo, "commit-graph", "write", "--stdin-commits")
    assert empty.returncode == 1
    assert "received no commits" in empty.stderr
    assert graph.graph_file.read_bytes() == before

    help_result = _run(repo, "commit-graph", "write", "--help")
    assert help_result.returncode == 0, help_result.stderr
    assert "--stdin-commits" in help_result.stdout
