"""Phase 98 tests: explicit ``update-ref --stdin`` transaction control."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from pygit import Repository
from pygit.command import dispatch
from pygit.ref_transaction import (
    RefUpdate,
    parse_update_records,
    set_symbolic_ref,
    symbolic_target,
    update_refs,
)
from pygit.refs import ZERO_SHA


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def _commit(repo: Repository, text: str, message: str) -> str:
    path = repo.worktree / "f.txt"
    path.write_text(text, encoding="utf-8")
    repo.add(["f.txt"])
    return repo.commit(message, author_name="Tester", author_email="tester@example.com")


def _commits(repo: Repository) -> tuple[str, str]:
    first = _commit(repo, "one\n", "first")
    second = _commit(repo, "two\n", "second")
    return first, second


def _run_stdin(repo: Repository, monkeypatch, text: str, *extra: str) -> int:
    monkeypatch.chdir(repo.worktree)
    monkeypatch.setattr("sys.stdin", io.StringIO(text))
    return dispatch(["update-ref", "--stdin", *extra])


def test_implicit_stdin_transaction_still_commits_at_eof(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    one, _ = _commits(repo)
    updates = parse_update_records([f"create refs/heads/topic {one}"])
    update_refs(repo, updates)
    assert repo.refs.get_branch("topic") == one


def test_started_transaction_auto_aborts_at_eof(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    one, _ = _commits(repo)
    updates = parse_update_records([
        "start",
        f"create refs/heads/topic {one}",
    ])
    update_refs(repo, updates)
    assert repo.refs.get_branch("topic") is None


def test_commit_can_close_started_transaction_and_start_another(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    one, two = _commits(repo)
    updates = parse_update_records([
        "start",
        f"create refs/heads/a {one}",
        "commit",
        "start",
        f"create refs/heads/b {two}",
        "commit",
    ])
    update_refs(repo, updates, message="session")
    assert repo.refs.get_branch("a") == one
    assert repo.refs.get_branch("b") == two
    assert repo.refs.read_reflog("refs/heads/a")[0].message == "session"
    assert repo.refs.read_reflog("refs/heads/b")[0].message == "session"


def test_abort_discards_only_current_transaction(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    one, two = _commits(repo)
    updates = parse_update_records([
        "start",
        f"create refs/heads/kept {one}",
        "commit",
        "start",
        f"create refs/heads/dropped {two}",
        "abort",
    ])
    update_refs(repo, updates)
    assert repo.refs.get_branch("kept") == one
    assert repo.refs.get_branch("dropped") is None


def test_prepare_preflights_without_publishing_then_commit_applies(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    one, two = _commits(repo)
    repo.refs.set_branch("topic", one)
    updates = parse_update_records([
        "start",
        f"update refs/heads/topic {two} {one}",
        "prepare",
    ])
    update_refs(repo, updates)
    assert repo.refs.get_branch("topic") == one

    updates = parse_update_records([
        "start",
        f"update refs/heads/topic {two} {one}",
        "prepare",
        "commit",
    ])
    update_refs(repo, updates)
    assert repo.refs.get_branch("topic") == two


def test_prepare_failure_keeps_every_ref_unchanged(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    one, two = _commits(repo)
    repo.refs.set_branch("a", one)
    repo.refs.set_branch("b", two)
    updates = parse_update_records([
        "start",
        f"update refs/heads/a {two} {one}",
        f"update refs/heads/b {one} {one}",
        "prepare",
        "commit",
    ])
    with pytest.raises(RuntimeError, match="expected"):
        update_refs(repo, updates)
    assert repo.refs.get_branch("a") == one
    assert repo.refs.get_branch("b") == two


def test_prepared_transaction_rejects_further_ref_commands(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    one, _ = _commits(repo)
    updates = parse_update_records([
        "start",
        "prepare",
        f"create refs/heads/late {one}",
    ])
    with pytest.raises(RuntimeError, match="prepared transactions can only be closed"):
        update_refs(repo, updates)
    assert repo.refs.get_branch("late") is None


def test_option_no_deref_applies_only_to_next_ref_command(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    one, two = _commits(repo)
    repo.refs.set_branch("target-a", one)
    repo.refs.set_branch("target-b", one)
    set_symbolic_ref(repo, "refs/aliases/a", "refs/heads/target-a")
    set_symbolic_ref(repo, "refs/aliases/b", "refs/heads/target-b")

    updates = parse_update_records([
        "option no-deref",
        f"update refs/aliases/a {two}",
        f"update refs/aliases/b {two} {one}",
    ])
    update_refs(repo, updates)

    assert symbolic_target(repo, "refs/aliases/a") is None
    assert (repo.pygit_dir / "refs" / "aliases" / "a").read_text(encoding="utf-8").strip() == two
    assert repo.refs.get_branch("target-a") == one
    assert symbolic_target(repo, "refs/aliases/b") == "refs/heads/target-b"
    assert repo.refs.get_branch("target-b") == two


def test_global_no_deref_remains_default_for_session(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    one, two = _commits(repo)
    repo.refs.set_branch("topic", one)
    set_symbolic_ref(repo, "refs/aliases/current", "refs/heads/topic")
    updates = parse_update_records([f"update refs/aliases/current {two}"])
    update_refs(repo, updates, deref=False)
    assert symbolic_target(repo, "refs/aliases/current") is None
    assert repo.refs.get_branch("topic") == one


def test_update_zero_oid_deletes_ref(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    one, _ = _commits(repo)
    repo.refs.set_branch("topic", one)
    updates = parse_update_records([f"update refs/heads/topic {ZERO_SHA} {one}"])
    update_refs(repo, updates)
    assert repo.refs.get_branch("topic") is None


def test_parser_rejects_unknown_control_option_and_arguments() -> None:
    with pytest.raises(ValueError, match="unsupported update-ref option"):
        parse_update_records(["option deref"])
    with pytest.raises(ValueError, match="takes no arguments"):
        parse_update_records(["start now"])


def test_cli_started_eof_aborts_and_explicit_commit_publishes(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    one, _ = _commits(repo)

    assert _run_stdin(
        repo,
        monkeypatch,
        f"start\ncreate refs/heads/aborted {one}\n",
    ) == 0
    assert repo.refs.get_branch("aborted") is None

    assert _run_stdin(
        repo,
        monkeypatch,
        f"start\ncreate refs/heads/committed {one}\nprepare\ncommit\n",
        "-m",
        "controlled",
    ) == 0
    assert repo.refs.get_branch("committed") == one
    assert repo.refs.read_reflog("refs/heads/committed")[0].message == "controlled"
