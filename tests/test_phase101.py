"""Phase 101 tests: partial-success ``update-ref --batch-updates``."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from pygit import Repository
from pygit.ref_batch import update_refs_batch
from pygit.ref_transaction import parse_update_records
from pygit.refs import ZERO_SHA


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def _commit(repo: Repository, text: str, message: str) -> str:
    path = repo.worktree / "f.txt"
    path.write_text(text, encoding="utf-8")
    repo.add(["f.txt"])
    return repo.commit(message, author_name="Tester", author_email="tester@example.com")


def _commits(repo: Repository) -> tuple[str, str]:
    one = _commit(repo, "one\n", "one")
    two = _commit(repo, "two\n", "two")
    return one, two


def _run(
    repo: Repository,
    *args: str,
    input_data: bytes = b"",
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", "update-ref", *args],
        cwd=repo.worktree,
        input=input_data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_api_rejects_bad_cas_but_commits_unrelated_update(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    one, two = _commits(repo)
    repo.refs.set_branch("a", one)
    repo.refs.set_branch("b", two)

    updates = parse_update_records([
        f"update refs/heads/a {two} {one}",
        f"update refs/heads/b {one} {one}",
    ])
    rejected = update_refs_batch(repo, updates)

    assert repo.refs.get_branch("a") == two
    assert repo.refs.get_branch("b") == two
    assert [item.format() for item in rejected] == [
        f"rejected refs/heads/b {one} {one} incorrect old value provided"
    ]


def test_create_exists_is_rejected_while_new_ref_is_created(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    one, two = _commits(repo)
    repo.refs.set_branch("existing", one)

    updates = parse_update_records([
        f"create refs/heads/existing {two}",
        f"create refs/heads/new {two}",
    ])
    rejected = update_refs_batch(repo, updates)

    assert repo.refs.get_branch("existing") == one
    assert repo.refs.get_branch("new") == two
    assert rejected[0].reason == "reference already exists"
    assert rejected[0].old_value == ZERO_SHA


def test_duplicate_physical_ref_rejects_only_later_update(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    one, two = _commits(repo)
    repo.refs.set_branch("topic", one)

    updates = parse_update_records([
        f"update refs/heads/topic {two} {one}",
        f"update refs/heads/topic {one} {one}",
    ])
    rejected = update_refs_batch(repo, updates)

    assert repo.refs.get_branch("topic") == two
    assert len(rejected) == 1
    assert rejected[0].reason == "name conflict"


def test_explicit_commit_reports_rejections_and_abort_discards_them(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    one, two = _commits(repo)
    repo.refs.set_branch("a", one)
    repo.refs.set_branch("b", two)

    updates = parse_update_records([
        "start",
        f"update refs/heads/a {two} {one}",
        f"update refs/heads/b {one} {one}",
        "commit",
        "start",
        f"update refs/heads/b {one} {one}",
        "abort",
    ])
    rejected = update_refs_batch(repo, updates)

    assert repo.refs.get_branch("a") == two
    assert repo.refs.get_branch("b") == two
    assert len(rejected) == 1
    assert rejected[0].refname == "refs/heads/b"


def test_explicit_transaction_at_eof_aborts_updates_and_rejections(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    one, two = _commits(repo)
    repo.refs.set_branch("a", one)
    repo.refs.set_branch("b", two)

    updates = parse_update_records([
        "start",
        f"update refs/heads/a {two} {one}",
        f"update refs/heads/b {one} {one}",
    ])
    assert update_refs_batch(repo, updates) == []
    assert repo.refs.get_branch("a") == one
    assert repo.refs.get_branch("b") == two


def test_non_transaction_input_error_remains_fatal_and_atomic(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    one, two = _commits(repo)
    repo.refs.set_branch("a", one)

    updates = parse_update_records([
        f"update refs/heads/a {two} {one}",
        "update refs/heads/b definitely-not-an-object",
    ])
    with pytest.raises(KeyError):
        update_refs_batch(repo, updates)
    assert repo.refs.get_branch("a") == one
    assert repo.refs.get_branch("b") is None


def test_system_failure_is_not_converted_to_rejection(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    one, _ = _commits(repo)
    updates = parse_update_records([f"create refs/heads/new {one}"])

    def fail_publish(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("pygit.ref_batch._apply_updates", fail_publish)
    with pytest.raises(OSError, match="disk full"):
        update_refs_batch(repo, updates)
    assert repo.refs.get_branch("new") is None


def test_installed_cli_batch_updates_emits_rejection_and_returns_success(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    one, two = _commits(repo)
    repo.refs.set_branch("a", one)
    repo.refs.set_branch("b", two)

    result = _run(
        repo,
        "--stdin",
        "--batch-updates",
        input_data=(
            f"update refs/heads/a {two} {one}\n"
            f"update refs/heads/b {one} {one}\n"
        ).encode("ascii"),
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert result.stdout == (
        f"rejected refs/heads/b {one} {one} incorrect old value provided\n"
    ).encode("ascii")
    assert repo.refs.get_branch("a") == two
    assert repo.refs.get_branch("b") == two


def test_nul_input_keeps_rejection_diagnostics_line_delimited(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    one, two = _commits(repo)
    repo.refs.set_branch("a", one)
    repo.refs.set_branch("b", two)

    data = (
        b"update refs/heads/a\0" + two.encode() + b"\0" + one.encode() + b"\0"
        + b"update refs/heads/b\0" + one.encode() + b"\0" + one.encode() + b"\0"
    )
    result = _run(repo, "--stdin", "-z", "--batch-updates", input_data=data)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert result.stdout.endswith(b"incorrect old value provided\n")
    assert b"\0" not in result.stdout
    assert repo.refs.get_branch("a") == two
    assert repo.refs.get_branch("b") == two


def test_batch_updates_requires_stdin(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    one, _ = _commits(repo)
    result = _run(repo, "--batch-updates", "refs/heads/x", one)
    assert result.returncode == 2
    assert b"--batch-updates requires --stdin" in result.stderr


def test_help_exposes_batch_updates(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    result = _run(repo, "--help")
    assert result.returncode == 0
    assert b"--batch-updates" in result.stdout
