"""Phase 116 tests: ``cat-file --follow-symlinks`` batch traversal."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pygit import Repository
from pygit.cat_file_symlink import (
    format_batch_object_follow_symlinks,
    resolve_follow_symlinks,
)
from pygit.objects import BlobObject, CommitObject, Identity, TreeEntry, TreeObject


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def _write_blob(repo: Repository, data: bytes) -> str:
    return repo.store.write(BlobObject(data))


def _snapshot(repo: Repository) -> tuple[str, str]:
    """Create a tree containing internal, escaping, dangling and loop links."""

    file_oid = _write_blob(repo, b"hello\n")
    link_file = _write_blob(repo, b"file")
    link_missing = _write_blob(repo, b"missing")
    link_escape = _write_blob(repo, b"../../outside")
    link_up = _write_blob(repo, b"../dir/file")
    link_root_file = _write_blob(repo, b"dir/file")
    link_outside = _write_blob(repo, b"../outside")
    link_absolute = _write_blob(repo, b"/etc/passwd")
    link_loop1 = _write_blob(repo, b"loop2")
    link_loop2 = _write_blob(repo, b"loop1")

    dir_tree = TreeObject(
        [
            TreeEntry("100644", "file", file_oid),
            TreeEntry("120000", "link", link_file),
            TreeEntry("120000", "dang", link_missing),
            TreeEntry("120000", "escape", link_escape),
        ]
    )
    dir_oid = repo.store.write(dir_tree)

    sub_tree = TreeObject([TreeEntry("120000", "up", link_up)])
    sub_oid = repo.store.write(sub_tree)

    root = TreeObject(
        [
            TreeEntry("040000", "dir", dir_oid),
            TreeEntry("040000", "sub", sub_oid),
            TreeEntry("120000", "rootlink", link_root_file),
            TreeEntry("120000", "outlink", link_outside),
            TreeEntry("120000", "abslink", link_absolute),
            TreeEntry("120000", "loop1", link_loop1),
            TreeEntry("120000", "loop2", link_loop2),
        ]
    )
    root_oid = repo.store.write(root)
    identity = Identity("Tester", "tester@example.com", 1, "+0000")
    commit_oid = repo.store.write(
        CommitObject(
            tree=root_oid,
            parents=[],
            author=identity,
            committer=identity,
            message="links",
        )
    )
    repo.refs.set_branch("main", commit_oid)
    return commit_oid, file_oid


def _run_bytes(
    repo: Repository,
    *args: str,
    input_bytes: bytes = b"",
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_internal_symlinks_follow_to_selected_object(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _, file_oid = _snapshot(repo)

    assert resolve_follow_symlinks(repo, "main:dir/link").oid == file_oid
    assert resolve_follow_symlinks(repo, "main:rootlink").oid == file_oid
    assert resolve_follow_symlinks(repo, "main:sub/up").oid == file_oid

    payload = format_batch_object_follow_symlinks(repo, "main:dir/link")
    assert payload == f"{file_oid} blob 6\n".encode("ascii")


def test_batch_contents_emit_followed_blob_not_link_blob(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _, file_oid = _snapshot(repo)

    result = _run_bytes(
        repo,
        "cat-file",
        "--batch",
        "--follow-symlinks",
        input_bytes=b"main:dir/link\n",
    )

    assert result.returncode == 0, result.stderr.decode()
    assert result.stderr == b""
    assert result.stdout == f"{file_oid} blob 6\nhello\n\n".encode("ascii")


def test_external_links_report_transformed_path_and_absolute_target(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    _snapshot(repo)

    assert format_batch_object_follow_symlinks(repo, "main:outlink") == (
        b"symlink 10\n../outside\n"
    )
    assert format_batch_object_follow_symlinks(repo, "main:dir/escape") == (
        b"symlink 10\n../outside\n"
    )
    assert format_batch_object_follow_symlinks(repo, "main:dir/escape/child") == (
        b"symlink 16\n../outside/child\n"
    )
    assert format_batch_object_follow_symlinks(repo, "main:abslink") == (
        b"symlink 11\n/etc/passwd\n"
    )


def test_dangling_loop_notdir_and_plain_missing_have_git_protocol_shapes(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    _snapshot(repo)

    assert format_batch_object_follow_symlinks(repo, "main:dir/dang") == (
        b"dangling 13\nmain:dir/dang\n"
    )
    assert format_batch_object_follow_symlinks(repo, "main:loop1") == (
        b"loop 10\nmain:loop1\n"
    )
    assert format_batch_object_follow_symlinks(repo, "main:dir/file/child") == (
        b"notdir 19\nmain:dir/file/child\n"
    )
    assert format_batch_object_follow_symlinks(repo, "main:nope") == (
        b"main:nope missing\n"
    )


def test_custom_format_applies_only_to_successful_object_records(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _, file_oid = _snapshot(repo)
    fmt = "X %(objectname) %(objecttype) %(objectsize)"

    success = format_batch_object_follow_symlinks(
        repo,
        "main:dir/link",
        format_string=fmt,
    )
    outside = format_batch_object_follow_symlinks(
        repo,
        "main:outlink",
        format_string=fmt,
    )

    assert success == f"X {file_oid} blob 6\n".encode("ascii")
    assert outside == b"symlink 10\n../outside\n"


def test_batch_command_follows_for_info_and_contents(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _, file_oid = _snapshot(repo)

    result = _run_bytes(
        repo,
        "cat-file",
        "--batch-command",
        "--follow-symlinks",
        input_bytes=(
            b"info main:dir/link\n"
            b"info main:outlink\n"
            b"contents main:dir/link\n"
        ),
    )

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout == (
        f"{file_oid} blob 6\n".encode("ascii")
        + b"symlink 10\n../outside\n"
        + f"{file_oid} blob 6\nhello\n\n".encode("ascii")
    )


def test_nul_framing_applies_to_special_and_success_records(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _, file_oid = _snapshot(repo)

    result = _run_bytes(
        repo,
        "cat-file",
        "--batch-check",
        "--follow-symlinks",
        "-Z",
        input_bytes=b"main:outlink\0main:dir/dang\0main:dir/link\0",
    )

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout == (
        b"symlink 10\0../outside\0"
        b"dangling 13\0main:dir/dang\0"
        + f"{file_oid} blob 6\0".encode("ascii")
    )


def test_follow_symlinks_requires_batch_mode_and_is_visible_in_help(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    commit_oid, _ = _snapshot(repo)

    invalid = _run_bytes(
        repo,
        "cat-file",
        "-t",
        "--follow-symlinks",
        commit_oid,
    )
    assert invalid.returncode == 2
    assert b"--follow-symlinks requires" in invalid.stderr

    help_result = _run_bytes(repo, "cat-file", "--help")
    assert help_result.returncode == 0
    assert b"--follow-symlinks" in help_result.stdout


def test_batch_all_objects_accepts_follow_symlinks_as_no_path_operation(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    _, file_oid = _snapshot(repo)

    result = _run_bytes(
        repo,
        "cat-file",
        "--batch-check",
        "--batch-all-objects",
        "--follow-symlinks",
    )

    assert result.returncode == 0, result.stderr.decode()
    assert f"{file_oid} blob 6\n".encode("ascii") in result.stdout
