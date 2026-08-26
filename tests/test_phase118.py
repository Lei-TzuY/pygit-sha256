"""Phase 118 tests: Git-style stage-zero ``:path`` object resolution."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from pygit import Repository
from pygit.index import IndexEntry
from pygit.index_revision import parse_index_expression, resolve_index_expression
from pygit.objects import BlobObject, CommitObject, Identity, TreeEntry, TreeObject
from pygit.revision import resolve_revision


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def _blob(repo: Repository, data: bytes) -> str:
    return repo.store.write(BlobObject(data))


def _entry(repo: Repository, path: str, oid: str, mode: str = "100644") -> None:
    repo.index.entries[path] = IndexEntry(path, oid, mode, 0, 0.0)
    repo.index.save()


def _head_with_file(repo: Repository, path: str, data: bytes) -> str:
    old_oid = _blob(repo, data)
    tree_oid = repo.store.write(TreeObject([TreeEntry("100644", path, old_oid)]))
    ident = Identity("Tester", "tester@example.com", 1, "+0000")
    commit_oid = repo.store.write(
        CommitObject(
            tree=tree_oid,
            parents=[],
            author=ident,
            committer=ident,
            message="base",
        )
    )
    repo.refs.set_branch("main", commit_oid)
    return old_oid


def _run(repo: Repository, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_index_namespace_is_distinct_from_head_tree(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    old_oid = _head_with_file(repo, "file.txt", b"old\n")
    staged_oid = _blob(repo, b"staged\n")
    _entry(repo, "file.txt", staged_oid)

    assert resolve_revision(repo, "HEAD:file.txt") == old_oid
    assert resolve_revision(repo, ":file.txt") == staged_oid
    assert resolve_revision(repo, ":0:file.txt") == staged_oid
    assert old_oid != staged_oid


def test_explicit_stage_zero_and_colons_in_paths_follow_git_prefix_rules(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    colon_oid = _blob(repo, b"colon")
    numeric_oid = _blob(repo, b"numeric")
    stage_looking_oid = _blob(repo, b"stage-looking")
    _entry(repo, "x:a", colon_oid)
    _entry(repo, "4:a", numeric_oid)
    _entry(repo, "0:a", stage_looking_oid)

    assert parse_index_expression(repo, ":x:a") == (0, "x:a")
    assert resolve_revision(repo, ":x:a") == colon_oid
    assert resolve_revision(repo, ":4:a") == numeric_oid
    assert resolve_revision(repo, ":0:0:a") == stage_looking_oid

    # ``:0:a`` is stage 0 of path ``a``; it is not the literal path ``0:a``.
    with pytest.raises(KeyError, match="not in the index"):
        resolve_revision(repo, ":0:a")


@pytest.mark.parametrize("stage", [1, 2, 3])
def test_unmerged_stage_syntax_is_valid_but_missing_stage_data_fails(
    tmp_path: Path,
    stage: int,
) -> None:
    repo = _repo(tmp_path)
    oid = _blob(repo, b"value")
    _entry(repo, "file.txt", oid)

    # Phase 118 originally rejected these stages because the index could not
    # represent them. Phase 122 makes the syntax live; absent stage data is now
    # a normal missing-index-stage error instead of an unsupported-feature error.
    with pytest.raises(KeyError, match=f"has no index stage {stage}"):
        resolve_revision(repo, f":{stage}:file.txt")


def test_cwd_relative_index_paths_follow_leading_dot_slash_and_dotdot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = _repo(tmp_path)
    (repo.worktree / "dir").mkdir()
    local_oid = _blob(repo, b"local")
    root_oid = _blob(repo, b"root")
    _entry(repo, "dir/local.txt", local_oid)
    _entry(repo, "root.txt", root_oid)

    monkeypatch.chdir(repo.worktree / "dir")

    # Plain paths remain repository-root-relative, matching native Git.
    with pytest.raises(KeyError, match="not in the index"):
        resolve_revision(repo, ":local.txt")
    assert resolve_revision(repo, ":dir/local.txt") == local_oid
    assert resolve_revision(repo, ":./local.txt") == local_oid
    assert resolve_revision(repo, ":../root.txt") == root_oid
    assert resolve_revision(repo, ":0:../root.txt") == root_oid


def test_cwd_relative_path_cannot_escape_repository(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    (repo.worktree / "dir").mkdir()
    monkeypatch.chdir(repo.worktree)

    with pytest.raises(ValueError, match="outside the repository"):
        resolve_revision(repo, ":../outside.txt")


def test_index_expression_composes_with_typed_peeling(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    oid = _blob(repo, b"payload")
    _entry(repo, "file.txt", oid)

    assert resolve_revision(repo, ":file.txt^{blob}") == oid
    assert resolve_revision(repo, ":file.txt^{object}") == oid
    with pytest.raises(RuntimeError, match="cannot be peeled to tree"):
        resolve_revision(repo, ":file.txt^{tree}")


def test_missing_index_entry_and_missing_backing_object_fail(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    with pytest.raises(KeyError, match="not in the index"):
        resolve_index_expression(repo, ":missing.txt")

    missing_oid = "f" * 64
    _entry(repo, "broken.txt", missing_oid)
    with pytest.raises(KeyError, match="names missing object"):
        resolve_revision(repo, ":broken.txt")


def test_installed_rev_parse_and_cat_file_share_index_resolution(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    oid = _blob(repo, b"hello\n")
    _entry(repo, "file.txt", oid)

    rev = _run(repo, "rev-parse", "--verify", ":file.txt")
    assert rev.returncode == 0, rev.stderr
    assert rev.stderr == ""
    assert rev.stdout == oid + "\n"

    typed = _run(repo, "rev-parse", "--verify", ":0:file.txt^{blob}")
    assert typed.returncode == 0, typed.stderr
    assert typed.stdout == oid + "\n"

    cat = _run(repo, "cat-file", "-p", ":file.txt")
    assert cat.returncode == 0, cat.stderr
    assert cat.stdout == "hello\n"


def test_installed_rev_parse_rejects_unavailable_conflict_stage(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    oid = _blob(repo, b"value")
    _entry(repo, "file.txt", oid)

    result = _run(repo, "rev-parse", "--verify", ":2:file.txt")

    assert result.returncode == 1
    assert result.stdout == ""
    assert "bad revision ':2:file.txt'" in result.stderr
