"""Phase 99 tests: NUL-framed ``update-ref --stdin -z`` protocol."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from pygit import Repository
from pygit.ref_transaction import RefUpdate, set_symbolic_ref, symbolic_target, update_refs
from pygit.refs import ZERO_SHA
from pygit.update_ref_protocol import parse_update_records_z


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def _commit(repo: Repository, text: str, message: str) -> str:
    path = repo.worktree / "f.txt"
    path.write_text(text, encoding="utf-8")
    repo.add(["f.txt"])
    return repo.commit(message, author_name="Tester", author_email="tester@example.com")


def _commits(repo: Repository) -> tuple[str, str]:
    return _commit(repo, "one\n", "first"), _commit(repo, "two\n", "second")


def _run(repo: Repository, *args: str, input_data: bytes = b"") -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", "update-ref", *args],
        cwd=repo.worktree,
        input=input_data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_parser_maps_nul_fields_to_direct_ref_updates() -> None:
    new = "1" * 64
    old = "2" * 64
    data = (
        b"start\0"
        b"option no-deref\0"
        b"update refs/heads/topic\0"
        + new.encode("ascii")
        + b"\0"
        + old.encode("ascii")
        + b"\0"
        + b"create refs/heads/new\0"
        + new.encode("ascii")
        + b"\0"
        + b"delete refs/heads/old\0\0"
        + b"verify refs/heads/check\0\0"
        + b"prepare\0commit\0"
    )
    assert parse_update_records_z(data) == [
        RefUpdate("start"),
        RefUpdate("option", "no-deref"),
        RefUpdate("update", "refs/heads/topic", new, old),
        RefUpdate("create", "refs/heads/new", new, ZERO_SHA),
        RefUpdate("delete", "refs/heads/old", None, None),
        RefUpdate("verify", "refs/heads/check", None, None),
        RefUpdate("prepare"),
        RefUpdate("commit"),
    ]


def test_parser_empty_optional_old_field_means_missing() -> None:
    new = "a" * 64
    assert parse_update_records_z(
        b"update refs/heads/topic\0" + new.encode("ascii") + b"\0\0"
    ) == [RefUpdate("update", "refs/heads/topic", new, None)]
    assert parse_update_records_z(b"delete refs/heads/topic\0\0") == [
        RefUpdate("delete", "refs/heads/topic", None, None)
    ]
    assert parse_update_records_z(b"verify refs/heads/topic\0\0") == [
        RefUpdate("verify", "refs/heads/topic", None, None)
    ]


def test_parser_empty_update_new_value_maps_to_zero_oid() -> None:
    assert parse_update_records_z(b"update refs/heads/topic\0\0\0") == [
        RefUpdate("update", "refs/heads/topic", ZERO_SHA, None)
    ]
    with pytest.raises(ValueError, match="create requires"):
        parse_update_records_z(b"create refs/heads/topic\0\0")


def test_parser_rejects_truncated_unknown_and_non_utf8_input() -> None:
    with pytest.raises(ValueError, match="end with NUL"):
        parse_update_records_z(b"start")
    with pytest.raises(ValueError, match="unexpected end"):
        parse_update_records_z(b"delete refs/heads/topic\0")
    with pytest.raises(ValueError, match="unsupported update-ref command"):
        parse_update_records_z(b"explode refs/heads/topic\0")
    with pytest.raises(ValueError, match="valid UTF-8"):
        parse_update_records_z(b"update refs/heads/topic\0\xff\0\0")


def test_empty_nul_stream_is_a_valid_noop() -> None:
    assert parse_update_records_z(b"") == []


def test_cli_nul_transaction_prepare_commit_updates_ref(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    one, two = _commits(repo)
    repo.refs.set_branch("topic", one)

    payload = (
        b"start\0update refs/heads/topic\0"
        + two.encode("ascii")
        + b"\0"
        + one.encode("ascii")
        + b"\0prepare\0commit\0"
    )
    result = _run(repo, "--stdin", "-z", "-m", "nul transaction", input_data=payload)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert result.stdout == b""
    assert repo.refs.get_branch("topic") == two
    assert repo.refs.read_reflog("refs/heads/topic")[0].message == "nul transaction"


def test_cli_nul_started_transaction_aborts_at_eof(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    one, _ = _commits(repo)
    payload = b"start\0create refs/heads/topic\0" + one.encode("ascii") + b"\0"
    result = _run(repo, "--stdin", "-z", input_data=payload)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert repo.refs.get_branch("topic") is None


def test_cli_nul_option_no_deref_is_one_shot(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    one, two = _commits(repo)
    repo.refs.set_branch("target-a", one)
    repo.refs.set_branch("target-b", one)
    set_symbolic_ref(repo, "refs/aliases/a", "refs/heads/target-a")
    set_symbolic_ref(repo, "refs/aliases/b", "refs/heads/target-b")

    payload = (
        b"option no-deref\0update refs/aliases/a\0"
        + two.encode("ascii")
        + b"\0\0update refs/aliases/b\0"
        + two.encode("ascii")
        + b"\0"
        + one.encode("ascii")
        + b"\0"
    )
    result = _run(repo, "--stdin", "-z", input_data=payload)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert symbolic_target(repo, "refs/aliases/a") is None
    assert repo.refs.get_branch("target-a") == one
    assert symbolic_target(repo, "refs/aliases/b") == "refs/heads/target-b"
    assert repo.refs.get_branch("target-b") == two


def test_cli_nul_zero_new_oid_deletes_ref(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    one, _ = _commits(repo)
    repo.refs.set_branch("topic", one)
    payload = (
        b"update refs/heads/topic\0"
        + ZERO_SHA.encode("ascii")
        + b"\0"
        + one.encode("ascii")
        + b"\0"
    )
    result = _run(repo, "--stdin", "-z", input_data=payload)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert repo.refs.get_branch("topic") is None


def test_cli_nul_cas_failure_is_atomic(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    one, two = _commits(repo)
    repo.refs.set_branch("a", one)
    repo.refs.set_branch("b", two)
    payload = (
        b"update refs/heads/a\0"
        + two.encode("ascii")
        + b"\0"
        + one.encode("ascii")
        + b"\0update refs/heads/b\0"
        + one.encode("ascii")
        + b"\0"
        + one.encode("ascii")
        + b"\0"
    )
    result = _run(repo, "--stdin", "-z", input_data=payload)
    assert result.returncode == 1
    assert b"expected" in result.stderr
    assert repo.refs.get_branch("a") == one
    assert repo.refs.get_branch("b") == two


def test_cli_rejects_z_without_stdin_and_truncated_stream(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    one, _ = _commits(repo)

    no_stdin = _run(repo, "-z", "refs/heads/topic", one)
    assert no_stdin.returncode == 2
    assert b"-z requires --stdin" in no_stdin.stderr

    truncated = _run(repo, "--stdin", "-z", input_data=b"delete refs/heads/topic\0")
    assert truncated.returncode == 1
    assert b"unexpected end of input" in truncated.stderr


def test_cli_help_exposes_nul_mode(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _commits(repo)
    result = _run(repo, "--help")
    assert result.returncode == 0
    assert b"-z" in result.stdout
    assert b"NUL-delimited" in result.stdout
