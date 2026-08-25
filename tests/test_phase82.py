"""Phase 82 tests: interactive ``cat-file --batch-command`` plumbing."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from pygit import (
    CatFileBatchCommand,
    Repository,
    format_batch_object,
    inspect_object,
    parse_batch_command,
    run_batch_commands,
)
from pygit.objects import BlobObject, CommitObject, Identity, TreeEntry, TreeObject


IDENT = Identity("Tester", "tester@example.com", 1, "+0000")


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def _fixture(repo: Repository) -> dict[str, object]:
    data = b"hello\x00world\n"
    blob = repo.store.write(BlobObject(data))
    tree = repo.store.write(TreeObject([TreeEntry("100644", "file.bin", blob)]))
    commit = repo.store.write(
        CommitObject(
            tree=tree,
            parents=[],
            author=IDENT,
            committer=IDENT,
            message="batch command fixture",
        )
    )
    repo.refs.set_branch("main", commit)
    repo.refs.set_head_symbolic("main")
    return {"data": data, "blob": blob, "tree": tree, "commit": commit}


def _run(
    repo: Repository,
    *args: str,
    input_data: bytes = b"",
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", "cat-file", *args],
        cwd=repo.worktree,
        input=input_data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_command_parser_preserves_payload_after_first_space() -> None:
    assert parse_batch_command("info HEAD\n") == CatFileBatchCommand("info", "HEAD")
    assert parse_batch_command("contents   HEAD:file.bin\r\n") == CatFileBatchCommand(
        "contents",
        "  HEAD:file.bin",
    )
    assert parse_batch_command("flush\n") == CatFileBatchCommand("flush")

    for raw in ("", "\n", "info\n", "contents\n", "info\tHEAD\n", "flush extra\n", "wat HEAD\n"):
        with pytest.raises(ValueError):
            parse_batch_command(raw)


def test_default_batch_payload_formats_info_contents_and_missing(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)
    blob = str(ids["blob"])
    data = ids["data"]
    assert isinstance(data, bytes)

    record = inspect_object(repo, blob)
    header = f"{record.oid} blob {len(data)}\n".encode("ascii")
    assert format_batch_object(repo, blob) == header
    assert format_batch_object(repo, blob, contents=True) == header + data + b"\n"
    assert format_batch_object(repo, "missing") == b"missing missing\n"


def test_run_batch_commands_yields_each_unbuffered_response(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)
    commit = str(ids["commit"])
    blob = str(ids["blob"])

    chunks = list(
        run_batch_commands(
            repo,
            [f"info {commit}\n", f"contents {blob}\n", "info missing\n"],
        )
    )
    assert chunks == [
        format_batch_object(repo, commit),
        format_batch_object(repo, blob, contents=True),
        b"missing missing\n",
    ]


def test_buffered_commands_flush_in_groups_and_at_clean_eof(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)
    commit = str(ids["commit"])
    blob = str(ids["blob"])

    chunks = list(
        run_batch_commands(
            repo,
            [
                f"info {commit}\n",
                f"contents {blob}\n",
                "flush\n",
                "info missing\n",
            ],
            buffered=True,
        )
    )
    assert chunks == [
        format_batch_object(repo, commit) + format_batch_object(repo, blob, contents=True),
        b"missing missing\n",
    ]


def test_flush_requires_buffer_and_buffered_parse_error_discards_pending(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)
    commit = str(ids["commit"])

    with pytest.raises(ValueError, match="--buffer"):
        list(run_batch_commands(repo, ["flush\n"]))

    with pytest.raises(ValueError, match="unknown"):
        list(
            run_batch_commands(
                repo,
                [f"info {commit}\n", "wat HEAD\n"],
                buffered=True,
            )
        )


def test_installed_cli_batch_command_info_contents_and_missing(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fixture(repo)
    input_data = b"info HEAD\ncontents HEAD:file.bin\ninfo missing\n"
    result = _run(repo, "--batch-command", input_data=input_data)

    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert result.stderr == b""
    assert result.stdout == (
        format_batch_object(repo, "HEAD")
        + format_batch_object(repo, "HEAD:file.bin", contents=True)
        + b"missing missing\n"
    )


def test_installed_cli_buffer_flush_and_error_boundaries(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fixture(repo)

    buffered = _run(
        repo,
        "--batch-command",
        "--buffer",
        input_data=b"info HEAD\ncontents HEAD:file.bin\nflush\ninfo missing\n",
    )
    assert buffered.returncode == 0, buffered.stderr.decode("utf-8", "replace")
    assert buffered.stdout == (
        format_batch_object(repo, "HEAD")
        + format_batch_object(repo, "HEAD:file.bin", contents=True)
        + b"missing missing\n"
    )

    failed = _run(
        repo,
        "--batch-command",
        "--buffer",
        input_data=b"info HEAD\nwat HEAD\n",
    )
    assert failed.returncode == 1
    assert failed.stdout == b""
    assert b"unknown cat-file batch command" in failed.stderr

    unbuffered_flush = _run(repo, "--batch-command", input_data=b"flush\n")
    assert unbuffered_flush.returncode == 1
    assert b"--buffer" in unbuffered_flush.stderr


def test_existing_batch_and_single_object_modes_still_route_correctly(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)
    data = ids["data"]
    assert isinstance(data, bytes)

    checked = _run(repo, "--batch-check", input_data=b"HEAD:file.bin\nmissing\n")
    assert checked.returncode == 0
    assert checked.stdout == format_batch_object(repo, "HEAD:file.bin") + b"missing missing\n"

    contents = _run(repo, "--batch", input_data=b"HEAD:file.bin\n")
    assert contents.returncode == 0
    assert contents.stdout == format_batch_object(repo, "HEAD:file.bin", contents=True)

    object_type = _run(repo, "-t", "HEAD:file.bin")
    assert object_type.returncode == 0
    assert object_type.stdout == b"blob\n"

    size = _run(repo, "-s", "HEAD:file.bin")
    assert size.returncode == 0
    assert size.stdout == f"{len(data)}\n".encode("ascii")

    missing = _run(repo, "-e", "missing")
    assert missing.returncode == 1
    assert missing.stdout == b""


def test_buffer_is_rejected_for_single_object_modes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fixture(repo)
    result = _run(repo, "-t", "--buffer", "HEAD")
    assert result.returncode == 2
    assert b"--buffer requires" in result.stderr
