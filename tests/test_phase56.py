"""Phase 56 tests: index-to-worktree checkout plumbing."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from pygit import Repository, checkout_index
from pygit.commit_plumbing import write_tree
from pygit.objects import BlobObject, CommitObject, Identity
from pygit.runtime import _run_checkout_index


def _repo(tmp_path: Path) -> Repository:
    repo = Repository.init(str(tmp_path / "r"))
    (repo.worktree / "a.txt").write_text("alpha\n", encoding="utf-8")
    (repo.worktree / "dir").mkdir()
    (repo.worktree / "dir" / "b.txt").write_text("beta\n", encoding="utf-8")
    repo.add(["a.txt", "dir/b.txt"])
    return repo


def test_checkout_selected_file_and_refuses_overwrite(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    target = repo.worktree / "a.txt"
    target.unlink()

    written = checkout_index(repo, ["a.txt"])
    assert written == [target]
    assert target.read_text(encoding="utf-8") == "alpha\n"

    target.write_text("dirty\n", encoding="utf-8")
    with pytest.raises(FileExistsError, match="use --force"):
        checkout_index(repo, ["a.txt"])
    assert target.read_text(encoding="utf-8") == "dirty\n"

    checkout_index(repo, ["a.txt"], force=True)
    assert target.read_text(encoding="utf-8") == "alpha\n"


def test_all_and_prefix_preserve_index_paths(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    written = checkout_index(repo, all_entries=True, prefix="export")

    assert written == [repo.worktree / "export" / "a.txt", repo.worktree / "export" / "dir" / "b.txt"]
    assert (repo.worktree / "export" / "a.txt").read_text(encoding="utf-8") == "alpha\n"
    assert (repo.worktree / "export" / "dir" / "b.txt").read_text(encoding="utf-8") == "beta\n"


def test_directory_prefix_and_glob_selection(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo.worktree / "dir" / "b.txt").unlink()
    checkout_index(repo, ["dir"])
    assert (repo.worktree / "dir" / "b.txt").read_text(encoding="utf-8") == "beta\n"

    (repo.worktree / "a.txt").unlink()
    (repo.worktree / "dir" / "b.txt").unlink()
    written = checkout_index(repo, ["*.txt"])
    assert written == [repo.worktree / "a.txt", repo.worktree / "dir" / "b.txt"]
    assert (repo.worktree / "a.txt").read_text(encoding="utf-8") == "alpha\n"
    assert (repo.worktree / "dir" / "b.txt").read_text(encoding="utf-8") == "beta\n"


def test_missing_pathspec_and_empty_selection_are_errors(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    with pytest.raises(ValueError, match="requires paths or --all"):
        checkout_index(repo)
    with pytest.raises(KeyError, match="did not match"):
        checkout_index(repo, ["missing.txt"])


def test_prefix_cannot_escape_or_write_inside_metadata(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    with pytest.raises(ValueError, match="outside the repository"):
        checkout_index(repo, ["a.txt"], prefix="../escape")
    with pytest.raises(ValueError, match="inside .pygit"):
        checkout_index(repo, ["a.txt"], prefix=".pygit/export")


@pytest.mark.skipif(os.name == "nt", reason="symlink creation is privilege-dependent on Windows")
def test_symlinked_parent_cannot_escape_worktree(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo.worktree / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlinked parent"):
        checkout_index(repo, ["a.txt"], prefix="escape")
    assert not (outside / "a.txt").exists()


def test_executable_mode_is_restored(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    entry = repo.index.get("a.txt")
    entry.mode = "100755"
    repo.index.save()
    (repo.worktree / "a.txt").unlink()

    checkout_index(repo, ["a.txt"])
    if os.name != "nt":
        assert (repo.worktree / "a.txt").stat().st_mode & stat.S_IXUSR


@pytest.mark.skipif(os.name == "nt", reason="symlink creation is privilege-dependent on Windows")
def test_symlink_entry_is_materialized(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    oid = repo.store.write(BlobObject(b"a.txt"))
    repo.index.entries["link"] = type(repo.index.get("a.txt"))("link", oid, "120000", 5, 0.0)
    repo.index.save()

    checkout_index(repo, ["link"])
    link = repo.worktree / "link"
    assert link.is_symlink()
    assert os.readlink(link) == "a.txt"


def test_submodule_entry_is_rejected(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    tree_oid = write_tree(repo)
    ident = Identity("A", "a@example.com")
    commit_oid = repo.store.write(CommitObject(tree=tree_oid, author=ident, committer=ident, message="m"))
    entry_type = type(repo.index.get("a.txt"))
    repo.index.entries["sub"] = entry_type("sub", commit_oid, "160000", 0, 0.0)
    repo.index.save()

    with pytest.raises(ValueError, match="submodule"):
        checkout_index(repo, ["sub"])


def test_cli_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo.worktree)
    (repo.worktree / "a.txt").unlink()

    assert _run_checkout_index(["a.txt"]) == 0
    assert (repo.worktree / "a.txt").read_text(encoding="utf-8") == "alpha\n"
