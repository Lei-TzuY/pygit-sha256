"""Phase 93 tests: binary-safe ``cat-file -Z`` batch framing."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pygit import Repository
from pygit.cat_file import format_batch_object, inspect_object, split_batch_input
from pygit.objects import BlobObject, CommitObject, Identity, TreeEntry, TreeObject


IDENT = Identity("Tester", "tester@example.com", 1, "+0000")


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def _fixture(repo: Repository) -> dict[str, object]:
    data = b"line one\nline two\x00tail"
    blob = repo.store.write(BlobObject(data))
    tree = repo.store.write(TreeObject([TreeEntry("100644", "file.bin", blob)]))
    commit = repo.store.write(
        CommitObject(
            tree=tree,
            parents=[],
            author=IDENT,
            committer=IDENT,
            message="zero framing fixture",
        )
    )
    repo.refs.set_branch("main", commit)
    repo.refs.set_head_symbolic("main")
    return {"blob": blob, "data": data, "tree": tree, "commit": commit}


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


def test_api_nul_framing_changes_only_protocol_terminators(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)
    blob = str(ids["blob"])
    data = ids["data"]
    assert isinstance(data, bytes)

    record = inspect_object(repo, blob)
    payload = format_batch_object(
        repo,
        blob,
        contents=True,
        record_terminator=b"\0",
    )
    assert payload == (
        f"{blob} blob {record.size}".encode("ascii")
        + b"\0"
        + data
        + b"\0"
    )
    assert b"line one\nline two\x00tail" in payload


def test_nul_input_stripping_preserves_embedded_newlines_and_rest() -> None:
    expression, rest = split_batch_input(
        "HEAD\tmeta\nline\0",
        "%(objectname)|%(rest)",
        record_terminator="\0",
    )
    assert expression == "HEAD"
    assert rest == "meta\nline"

    expression, rest = split_batch_input(
        "HEAD:path\nsegment\0",
        "%(objectname)",
        record_terminator="\0",
    )
    assert expression == "HEAD:path\nsegment"
    assert rest == ""


def test_installed_batch_check_uses_nul_input_and_output(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)
    blob = str(ids["blob"])
    record = inspect_object(repo, blob)

    result = _run(
        repo,
        "--batch-check",
        "-Z",
        input_data=(blob + "\0missing\0").encode("ascii"),
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert result.stderr == b""
    assert result.stdout == (
        f"{blob} blob {record.size}".encode("ascii")
        + b"\0missing missing\0"
    )
    assert b"\n" not in result.stdout


def test_custom_format_preserves_literal_newline_under_zero_framing(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)
    blob = str(ids["blob"])

    result = _run(
        repo,
        "--batch-check=%(objectname)|%(rest)\nEND",
        "-Z",
        input_data=(blob + "\tmeta\nline\0").encode("ascii"),
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert result.stdout == (
        f"{blob}|meta\nline\nEND".encode("ascii") + b"\0"
    )


def test_batch_contents_preserve_binary_blob_bytes_under_zero_framing(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)
    blob = str(ids["blob"])
    data = ids["data"]
    assert isinstance(data, bytes)
    record = inspect_object(repo, blob)

    result = _run(repo, "--batch", "-Z", input_data=(blob + "\0").encode("ascii"))
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert result.stdout == (
        f"{blob} blob {record.size}".encode("ascii")
        + b"\0"
        + data
        + b"\0"
    )


def test_batch_command_zero_framing_composes_with_buffer_and_flush(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)
    blob = str(ids["blob"])
    data = ids["data"]
    assert isinstance(data, bytes)
    record = inspect_object(repo, blob)

    result = _run(
        repo,
        "--batch-command",
        "--buffer",
        "-Z",
        input_data=(f"info {blob}\0contents {blob}\0flush\0").encode("ascii"),
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    expected_header = f"{blob} blob {record.size}".encode("ascii") + b"\0"
    assert result.stdout == expected_header + expected_header + data + b"\0"


def test_batch_all_objects_zero_framing_ignores_stdin_and_uses_nul_records(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)
    expected = sorted(str(ids[name]) for name in ("blob", "tree", "commit"))

    result = _run(
        repo,
        "--batch-check=%(objectname)",
        "--batch-all-objects",
        "-Z",
        input_data=b"not-a-command\0still-ignored\0",
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    records = result.stdout.split(b"\0")
    assert records[-1] == b""
    assert [item.decode("ascii") for item in records[:-1]] == expected


def test_zero_framing_requires_batch_mode(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)

    result = _run(repo, "-t", "-Z", str(ids["blob"]))
    assert result.returncode == 2
    assert result.stdout == b""
    assert b"-Z requires --batch" in result.stderr


def test_zero_framing_help_is_exposed(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fixture(repo)

    result = _run(repo, "--help")
    assert result.returncode == 0
    assert b"-Z" in result.stdout
    assert b"NUL" in result.stdout
