"""Phase 122 tests: Git-style ``ls-tree -l/--long`` output."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from pygit import Repository, ls_tree, repack
from pygit.ls_tree_long import format_ls_tree_long
from pygit.objects import BlobObject, CommitObject, Identity, TreeEntry, TreeObject


IDENT = Identity("Tester", "tester@example.com", 1, "+0000")


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def _commit(repo: Repository, tree: str, message: str = "commit") -> str:
    return repo.store.write(
        CommitObject(
            tree=tree,
            parents=[],
            author=IDENT,
            committer=IDENT,
            message=message,
        )
    )


def _fixture(repo: Repository) -> dict[str, str]:
    plain = repo.store.write(BlobObject(b"abc\n"))
    symlink = repo.store.write(BlobObject(b"file.txt"))
    nested = repo.store.write(BlobObject(b"hello"))
    nested_tree = repo.store.write(
        TreeObject([TreeEntry("100644", "nested.txt", nested)])
    )
    gitlink_tree = repo.store.write(TreeObject([]))
    gitlink_commit = _commit(repo, gitlink_tree, "gitlink")
    root = repo.store.write(
        TreeObject(
            [
                TreeEntry("100644", "file.txt", plain),
                TreeEntry("120000", "link", symlink),
                TreeEntry("160000", "module", gitlink_commit),
                TreeEntry("040000", "sub", nested_tree),
            ]
        )
    )
    commit = _commit(repo, root)
    repo.refs.set_branch("main", commit)
    repo.refs.set_head_symbolic("main")
    return {
        "plain": plain,
        "symlink": symlink,
        "nested": nested,
        "nested_tree": nested_tree,
        "gitlink_commit": gitlink_commit,
        "root": root,
        "commit": commit,
    }


def _run(repo: Repository, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_long_formatter_matches_native_size_shape(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)

    data = format_ls_tree_long(repo, ls_tree(repo, "HEAD"))

    assert data == (
        f"100644 blob {ids['plain']}       4\tfile.txt\n"
        f"120000 blob {ids['symlink']}       8\tlink\n"
        f"160000 commit {ids['gitlink_commit']}       -\tmodule\n"
        f"040000 tree {ids['nested_tree']}       -\tsub\n"
    ).encode("ascii")


def test_recursive_show_trees_long_mode_sizes_only_blobs(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)

    entries = ls_tree(repo, "HEAD", recursive=True, show_trees=True)
    data = format_ls_tree_long(repo, entries, abbrev=8).decode("ascii")
    lines = data.splitlines()

    assert any(line.startswith("040000 tree ") and "       -\tsub" in line for line in lines)
    assert any(line.startswith("100644 blob ") and "       5\tsub/nested.txt" in line for line in lines)
    nested_line = next(line for line in lines if line.endswith("\tsub/nested.txt"))
    abbreviated = nested_line.split()[2]
    assert len(abbreviated) >= 8
    assert ids["nested"].startswith(abbreviated)


def test_long_nul_framing_uses_nul_record_terminators(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)
    entries = ls_tree(repo, "HEAD", patterns=("file.txt", "sub"))

    data = format_ls_tree_long(repo, entries, nul_terminated=True)

    assert data == (
        f"100644 blob {ids['plain']}       4\tfile.txt\0"
        f"040000 tree {ids['nested_tree']}       -\tsub\0"
    ).encode("ascii")


def test_long_reads_blob_size_from_packed_only_storage(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)
    repack(repo, all_objects=True, delete_redundant=True)
    loose = repo.store.root / ids["plain"][:2] / ids["plain"][2:]
    assert not loose.exists()

    entries = ls_tree(repo, "HEAD", patterns=("file.txt",))
    data = format_ls_tree_long(repo, entries)

    assert data == f"100644 blob {ids['plain']}       4\tfile.txt\n".encode("ascii")


def test_tree_and_gitlink_sizes_do_not_require_leaf_object_reads(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    missing_commit = "a" * 64
    child = repo.store.write(TreeObject([]))
    root = repo.store.write(
        TreeObject(
            [
                TreeEntry("160000", "module", missing_commit),
                TreeEntry("040000", "sub", child),
            ]
        )
    )

    data = format_ls_tree_long(repo, ls_tree(repo, root))

    assert f"160000 commit {missing_commit}       -\tmodule\n".encode("ascii") in data
    assert f"040000 tree {child}       -\tsub\n".encode("ascii") in data


def test_blob_mode_pointing_at_non_blob_fails_when_size_is_requested(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    child = repo.store.write(TreeObject([]))
    root = repo.store.write(TreeObject([TreeEntry("100644", "bad", child)]))

    with pytest.raises(RuntimeError, match="non-blob"):
        format_ls_tree_long(repo, ls_tree(repo, root))


def test_installed_cli_long_mode_and_help(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)

    result = _run(repo, "ls-tree", "-l", "HEAD", "--", "file.txt")
    assert result.returncode == 0, result.stderr.decode()
    assert result.stderr == b""
    assert result.stdout == (
        f"100644 blob {ids['plain']}       4\tfile.txt\n".encode("ascii")
    )

    help_result = _run(repo, "ls-tree", "--help")
    assert help_result.returncode == 0
    assert b"-l" in help_result.stdout
    assert b"--long" in help_result.stdout


def test_installed_cli_long_composes_with_recursive_abbrev_and_nul(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)

    result = _run(repo, "ls-tree", "-lrtz", "--abbrev=8", "HEAD")
    assert result.returncode == 0, result.stderr.decode()
    records = [record for record in result.stdout.split(b"\0") if record]
    assert any(record.endswith(b"       -\tsub") for record in records)
    nested = next(record for record in records if record.endswith(b"       5\tsub/nested.txt"))
    abbreviated = nested.split()[2].decode("ascii")
    assert len(abbreviated) >= 8
    assert ids["nested"].startswith(abbreviated)


def test_long_is_mutually_exclusive_with_other_output_modes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fixture(repo)

    for option in ("--name-only", "--object-only", "--format=%(path)"):
        result = _run(repo, "ls-tree", "-l", option, "HEAD")
        assert result.returncode == 2
        assert b"not allowed with argument" in result.stderr
