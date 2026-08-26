"""Phase 139 tests: rev-list --timestamp presentation."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pygit import Repository
from pygit.objects import BlobObject, CommitObject, Identity, TreeEntry, TreeObject


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
    blob = repo.store.write(BlobObject(b"timestamp\n"))
    tree = repo.store.write(TreeObject([TreeEntry("100644", "file.txt", blob)]))
    root = _commit(repo, tree, [], "root", 100)
    left = _commit(repo, tree, [root], "left", 200)
    right = _commit(repo, tree, [root], "right", 300)
    merge = _commit(repo, tree, [left, right], "merge", 400)
    repo.refs.set_branch("left", left)
    repo.refs.set_branch("right", right)
    repo.refs.set_branch("main", merge)
    repo.refs.set_head_symbolic("main")
    return repo, {"root": root, "left": left, "right": right, "merge": merge, "tree": tree, "blob": blob}


def _run(repo: Repository, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_timestamp_prefixes_plain_commit_records(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)
    result = _run(repo, "rev-list", "--timestamp", "--topo-order", "main")
    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout.decode().splitlines() == [
        f"400 {h['merge']}",
        f"300 {h['right']}",
        f"200 {h['left']}",
        f"100 {h['root']}",
    ]


def test_timestamp_precedes_side_and_boundary_markers(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)
    result = _run(repo, "rev-list", "--timestamp", "--left-right", "--boundary", "--topo-order", "left...right")
    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout.decode().splitlines() == [
        f"300 >{h['right']}",
        f"200 <{h['left']}",
        f"100 -{h['root']}",
    ]


def test_timestamp_composes_with_parents(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)
    result = _run(repo, "rev-list", "--timestamp", "--parents", "-n", "1", "main")
    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout == f"400 {h['merge']} {h['left']} {h['right']}\n".encode()


def test_timestamp_composes_with_children(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)
    result = _run(repo, "rev-list", "--timestamp", "--children", "--topo-order", "main")
    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout.decode().splitlines()[-1] == f"100 {h['root']} {h['right']} {h['left']}"


def test_timestamp_decorates_commit_records_only_in_object_mode(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)
    result = _run(repo, "rev-list", "--timestamp", "--objects", "-n", "1", "main")
    assert result.returncode == 0, result.stderr.decode()
    lines = result.stdout.decode().splitlines()
    assert lines[0] == f"400 {h['merge']}"
    assert any(line.startswith(h["tree"]) for line in lines[1:])
    assert any(line == f"{h['blob']} file.txt" for line in lines[1:])
    assert not any(line.startswith("400 ") for line in lines[1:])


def test_objects_edge_boundary_record_stays_untimestamped(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)
    result = _run(repo, "rev-list", "--timestamp", "--objects-edge", "left..main")
    assert result.returncode == 0, result.stderr.decode()
    lines = result.stdout.decode().splitlines()
    assert lines[0] == f"-{h['left']}"
    assert any(line == f"400 {h['merge']}" for line in lines)


def test_count_suppresses_timestamp_presentation(tmp_path: Path) -> None:
    repo, _ = _graph(tmp_path)
    plain = _run(repo, "rev-list", "--count", "main")
    stamped = _run(repo, "rev-list", "--timestamp", "--count", "main")
    assert plain.returncode == stamped.returncode == 0
    assert stamped.stdout == plain.stdout == b"4\n"


def test_help_advertises_timestamp_option(tmp_path: Path) -> None:
    repo, _ = _graph(tmp_path)
    result = _run(repo, "rev-list", "--help")
    assert result.returncode == 0
    assert b"--timestamp" in result.stdout
