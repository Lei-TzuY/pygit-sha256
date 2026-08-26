"""Phase 127: write-tree must never serialize an unmerged index."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from pygit import Repository
from pygit.commit_plumbing import write_tree
from pygit.index import IndexEntry
from pygit.objects import BlobObject, TreeObject


def _repo(path: Path) -> Repository:
    return Repository.init(str(path))


def _stage_blob(
    repo: Repository,
    path: str,
    data: bytes,
    *,
    stage: int = 0,
    resolve_path: bool = False,
) -> str:
    oid = repo.store.write(BlobObject(data))
    repo.index.set_entry(
        IndexEntry(path=path, sha=oid, mode="100644", size=len(data), stage=stage),
        resolve_path=resolve_path,
    )
    repo.index.save()
    return oid


def _stage_conflict(repo: Repository, path: str) -> tuple[str, str, str]:
    return tuple(
        _stage_blob(repo, path, payload, stage=stage)
        for stage, payload in (
            (1, b"base\n"),
            (2, b"ours\n"),
            (3, b"theirs\n"),
        )
    )  # type: ignore[return-value]


def test_write_tree_rejects_unmerged_index_before_writing_objects(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    _stage_blob(repo, "clean.txt", b"clean\n")
    _stage_conflict(repo, "conflict.txt")

    before = set(repo.store.all_shas())
    with pytest.raises(RuntimeError, match=r"unmerged index entries: conflict\.txt"):
        write_tree(repo)

    assert set(repo.store.all_shas()) == before
    assert [entry.stage for entry in repo.index.stage_entries("conflict.txt")] == [1, 2, 3]


def test_prefix_and_missing_ok_cannot_hide_unmerged_paths(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    _stage_blob(repo, "src/ok.txt", b"ok\n")

    # Deliberately use absent object IDs: the unmerged guard must run before
    # --missing-ok or prefix filtering can otherwise make these entries vanish.
    for stage, digit in ((1, "1"), (2, "2"), (3, "3")):
        repo.index.set_entry(
            IndexEntry(
                path="outside/conflict.txt",
                sha=digit * 64,
                mode="100644",
                stage=stage,
            )
        )
    repo.index.save()

    before = set(repo.store.all_shas())
    with pytest.raises(RuntimeError, match=r"outside/conflict\.txt"):
        write_tree(repo, missing_ok=True, prefix="src")
    assert set(repo.store.all_shas()) == before


def test_resolving_conflict_to_stage_zero_allows_write_tree(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    _stage_blob(repo, "clean.txt", b"clean\n")
    _stage_conflict(repo, "conflict.txt")

    resolved_oid = _stage_blob(
        repo,
        "conflict.txt",
        b"resolved\n",
        stage=0,
        resolve_path=True,
    )
    assert not repo.index.has_unmerged("conflict.txt")

    tree_oid = write_tree(repo)
    tree = repo.store.read(tree_oid)
    assert isinstance(tree, TreeObject)
    entries = {entry.name: entry.sha for entry in tree.entries}
    assert entries["conflict.txt"] == resolved_oid
    assert "clean.txt" in entries


def test_installed_write_tree_reports_each_unmerged_path_once(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    _stage_blob(repo, "clean.txt", b"clean\n")
    _stage_conflict(repo, "z-conflict.txt")
    _stage_conflict(repo, "a-conflict.txt")

    result = subprocess.run(
        [sys.executable, "-m", "pygit", "write-tree", "--missing-ok"],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        text=True,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert "cannot write tree with unmerged index entries" in result.stderr
    assert result.stderr.count("a-conflict.txt") == 1
    assert result.stderr.count("z-conflict.txt") == 1
    assert result.stderr.index("a-conflict.txt") < result.stderr.index("z-conflict.txt")
