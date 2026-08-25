"""Phase 84 tests: custom ``cat-file`` batch response formats."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from pygit import (
    Repository,
    batch_format_uses_rest,
    format_batch_object,
    format_batch_record,
    inspect_object,
    run_batch_commands,
    split_batch_input,
)
from pygit.objects import BlobObject, CommitObject, Identity, TreeEntry, TreeObject


IDENT = Identity("Tester", "tester@example.com", 1, "+0000")


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def _fixture(repo: Repository) -> dict[str, object]:
    data = b"custom\x00format\n"
    blob = repo.store.write(BlobObject(data))
    spaced = repo.store.write(BlobObject(b"spaced path\n"))
    tree = repo.store.write(
        TreeObject(
            [
                TreeEntry("100644", "file.bin", blob),
                TreeEntry("100644", "a b.txt", spaced),
            ]
        )
    )
    commit = repo.store.write(
        CommitObject(
            tree=tree,
            parents=[],
            author=IDENT,
            committer=IDENT,
            message="custom batch format fixture",
        )
    )
    repo.refs.set_branch("main", commit)
    repo.refs.set_head_symbolic("main")
    return {"data": data, "blob": blob, "spaced": spaced, "tree": tree, "commit": commit}


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


def test_api_formats_supported_atoms_literals_and_percent_escaping(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)
    record = inspect_object(repo, str(ids["blob"]))
    rendered = format_batch_record(
        record,
        "%% %(objectname)|%(objecttype)|%(objectsize)|%(rest)|%x41|%%(objectname)",
        rest="tail data",
    )
    assert rendered == (
        f"% {record.oid}|blob|{record.size}|tail data|%x41|%(objectname)\n".encode("utf-8")
    )


def test_format_validation_rejects_unknown_and_unterminated_atoms_before_lookup(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fixture(repo)
    with pytest.raises(ValueError, match="unsupported.*atom"):
        format_batch_object(repo, "missing", format_string="%(unknown)")
    with pytest.raises(ValueError, match="unterminated"):
        format_batch_object(repo, "missing", format_string="%(objectname")


def test_rest_input_split_matches_git_whitespace_rules() -> None:
    fmt = "%(objectname)|%(rest)"
    assert batch_format_uses_rest(fmt)
    assert not batch_format_uses_rest("%(objectname)")
    assert split_batch_input("HEAD    extra  stuff\n", fmt) == ("HEAD", "extra  stuff")
    assert split_batch_input("HEAD\textra\tstuff\r\n", fmt) == ("HEAD", "extra\tstuff")
    assert split_batch_input("HEAD\n", fmt) == ("HEAD", "")
    assert split_batch_input("HEAD:a b.txt\n", "%(objectname)") == ("HEAD:a b.txt", "")


def test_custom_missing_records_ignore_format_and_rest(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fixture(repo)
    assert format_batch_object(
        repo,
        "missing",
        format_string="prefix %(objectname) %(rest)",
        rest="ignored tail",
    ) == b"missing missing\n"


def test_installed_batch_check_custom_format_and_rest(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)
    blob = str(ids["blob"])
    data = ids["data"]
    assert isinstance(data, bytes)
    fmt = "%(objectname)|%(objecttype)|%(objectsize)|%(rest)"
    result = _run(
        repo,
        f"--batch-check={fmt}",
        input_data=(f"{blob}    metadata  more\nmissing extra words\n").encode("ascii"),
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert result.stderr == b""
    assert result.stdout == (
        f"{blob}|blob|{len(data)}|metadata  more\n".encode("ascii")
        + b"missing missing\n"
    )


def test_without_rest_atom_spaced_revision_path_remains_one_expression(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)
    spaced = str(ids["spaced"])
    result = _run(
        repo,
        "--batch-check=%(objectname)|%(objecttype)",
        input_data=b"HEAD:a b.txt\n",
    )
    assert result.returncode == 0
    assert result.stdout == f"{spaced}|blob\n".encode("ascii")


def test_batch_contents_uses_custom_header_before_binary_payload(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)
    blob = str(ids["blob"])
    data = ids["data"]
    assert isinstance(data, bytes)
    result = _run(
        repo,
        "--batch=%(objecttype):%(objectsize)",
        input_data=(blob + "\n").encode("ascii"),
    )
    assert result.returncode == 0
    assert result.stdout == f"blob:{len(data)}\n".encode("ascii") + data + b"\n"


def test_empty_custom_format_is_valid_and_emits_only_record_newline(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)
    blob = str(ids["blob"])
    result = _run(repo, "--batch-check=", input_data=(blob + "\n").encode("ascii"))
    assert result.returncode == 0
    assert result.stdout == b"\n"


def test_batch_command_custom_format_preserves_command_expression_semantics(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)
    blob = str(ids["blob"])
    data = ids["data"]
    assert isinstance(data, bytes)
    fmt = "%(objectname)|%(objecttype)|%(objectsize)|%(rest)"
    chunks = list(
        run_batch_commands(
            repo,
            ["info HEAD\n", f"contents {blob}\n", "info HEAD extra\n"],
            format_string=fmt,
        )
    )
    head = inspect_object(repo, "HEAD")
    assert chunks == [
        f"{head.oid}|commit|{head.size}|\n".encode("ascii"),
        f"{blob}|blob|{len(data)}|\n".encode("ascii") + data + b"\n",
        b"HEAD extra missing\n",
    ]


def test_installed_batch_command_custom_format_composes_with_buffer(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)
    blob = str(ids["blob"])
    fmt = "%(objecttype):%(objectsize)"
    result = _run(
        repo,
        f"--batch-command={fmt}",
        "--buffer",
        input_data=(f"info {blob}\ninfo missing\nflush\n").encode("ascii"),
    )
    record = inspect_object(repo, blob)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert result.stdout == f"blob:{record.size}\n".encode("ascii") + b"missing missing\n"


def test_bad_custom_format_fails_before_stdin_can_emit_partial_output(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fixture(repo)
    result = _run(
        repo,
        "--batch-check=%(unknown)",
        input_data=b"HEAD\n",
    )
    assert result.returncode == 1
    assert result.stdout == b""
    assert b"unsupported cat-file batch format atom" in result.stderr


def test_optional_format_must_be_attached_with_equals(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fixture(repo)
    result = _run(
        repo,
        "--batch-check",
        "%(objectname)",
        input_data=b"HEAD\n",
    )
    assert result.returncode == 2
    assert b"batch modes read object names" in result.stderr


def test_default_batch_format_remains_unchanged(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)
    blob = str(ids["blob"])
    result = _run(repo, "--batch-check", input_data=(blob + "\n").encode("ascii"))
    assert result.returncode == 0
    assert result.stdout == format_batch_object(repo, blob)
