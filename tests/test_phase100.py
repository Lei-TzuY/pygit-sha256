"""Phase 100 tests: update-ref symbolic-ref transaction commands."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from pygit import Repository
from pygit.objects import CommitObject, Identity, TreeObject
from pygit.ref_transaction import parse_update_records, set_symbolic_ref, symbolic_target, update_refs
from pygit.update_ref_protocol import parse_update_records_z


IDENT = Identity("Tester", "tester@example.com", 1, "+0000")


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def _commit(repo: Repository, message: str, parents: list[str] | None = None) -> str:
    tree = repo.store.write(TreeObject())
    return repo.store.write(
        CommitObject(
            tree=tree,
            parents=parents or [],
            author=IDENT,
            committer=IDENT,
            message=message,
        )
    )


def _fixture(repo: Repository) -> dict[str, str]:
    first = _commit(repo, "first")
    second = _commit(repo, "second", [first])
    repo.refs.set_branch("main", first)
    repo.refs.set_branch("other", first)
    repo.refs.set_head_symbolic("main")
    return {"first": first, "second": second}


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


def test_line_parser_supports_all_symref_commands() -> None:
    updates = parse_update_records(
        [
            "symref-create refs/meta/a refs/heads/main",
            "symref-update refs/meta/b refs/heads/main ref refs/heads/old",
            "symref-update refs/meta/c refs/heads/main oid " + "1" * 64,
            "symref-delete refs/meta/d refs/heads/main",
            "symref-verify refs/meta/e refs/heads/main",
        ]
    )
    assert [item.action for item in updates] == [
        "symref-create",
        "symref-update",
        "symref-update",
        "symref-delete",
        "symref-verify",
    ]
    assert updates[1].old_kind == "ref"
    assert updates[1].old_target == "refs/heads/old"
    assert updates[2].old_kind == "oid"
    assert updates[2].old_oid == "1" * 64


def test_implicit_transaction_can_mix_direct_and_symbolic_creates(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)
    updates = parse_update_records(
        [
            f"create refs/heads/release {ids['first']}",
            "symref-create refs/meta/current refs/heads/release",
        ]
    )
    update_refs(repo, updates)
    assert repo.refs.get_branch("release") == ids["first"]
    assert symbolic_target(repo, "refs/meta/current") == "refs/heads/release"


def test_mixed_transaction_failure_rolls_back_direct_change(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)
    set_symbolic_ref(repo, "refs/meta/current", "refs/heads/main")

    updates = parse_update_records(
        [
            "start",
            f"update refs/heads/main {ids['second']} {ids['first']}",
            "option no-deref",
            "symref-update refs/meta/current refs/heads/other ref refs/heads/wrong",
            "commit",
        ]
    )
    with pytest.raises(RuntimeError, match="expected symbolic target"):
        update_refs(repo, updates)

    assert repo.refs.get_branch("main") == ids["first"]
    assert symbolic_target(repo, "refs/meta/current") == "refs/heads/main"


def test_symref_update_default_dereferences_before_replacing(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)
    set_symbolic_ref(repo, "refs/meta/current", "refs/heads/main")

    update_refs(
        repo,
        parse_update_records(
            [f"symref-update refs/meta/current refs/heads/other oid {ids['first']}"]
        ),
    )

    # Default deref mode updates the physical referent, matching native Git.
    assert symbolic_target(repo, "refs/meta/current") == "refs/heads/main"
    assert symbolic_target(repo, "refs/heads/main") == "refs/heads/other"


def test_option_no_deref_updates_symbolic_ref_itself(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fixture(repo)
    set_symbolic_ref(repo, "refs/meta/current", "refs/heads/main")

    update_refs(
        repo,
        parse_update_records(
            [
                "option no-deref",
                "symref-update refs/meta/current refs/heads/other ref refs/heads/main",
            ]
        ),
    )
    assert symbolic_target(repo, "refs/meta/current") == "refs/heads/other"
    assert symbolic_target(repo, "refs/heads/main") is None


def test_symref_delete_and_verify_require_no_deref(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fixture(repo)
    set_symbolic_ref(repo, "refs/meta/current", "refs/heads/main")

    with pytest.raises(ValueError, match="cannot operate with deref mode"):
        update_refs(
            repo,
            parse_update_records(["symref-verify refs/meta/current refs/heads/main"]),
        )

    update_refs(
        repo,
        parse_update_records(
            ["option no-deref", "symref-verify refs/meta/current refs/heads/main"]
        ),
    )
    update_refs(
        repo,
        parse_update_records(
            ["option no-deref", "symref-delete refs/meta/current refs/heads/main"]
        ),
    )
    assert symbolic_target(repo, "refs/meta/current") is None
    assert not (repo.pygit_dir / "refs" / "meta" / "current").exists()


def test_projected_symref_cycle_is_rejected_atomically(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fixture(repo)
    updates = parse_update_records(
        [
            "symref-create refs/meta/a refs/meta/b",
            "symref-create refs/meta/b refs/meta/a",
        ]
    )
    with pytest.raises(RuntimeError, match="cycle"):
        update_refs(repo, updates)
    assert not (repo.pygit_dir / "refs" / "meta" / "a").exists()
    assert not (repo.pygit_dir / "refs" / "meta" / "b").exists()


def test_prepare_then_abort_mixed_transaction_publishes_nothing(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)
    updates = parse_update_records(
        [
            "start",
            f"update refs/heads/main {ids['second']} {ids['first']}",
            "symref-create refs/meta/current refs/heads/main",
            "prepare",
            "abort",
        ]
    )
    update_refs(repo, updates)
    assert repo.refs.get_branch("main") == ids["first"]
    assert not (repo.pygit_dir / "refs" / "meta" / "current").exists()


def test_nul_parser_supports_symref_update_old_ref_and_old_oid() -> None:
    oid = "a" * 64
    records = parse_update_records_z(
        b"symref-update refs/meta/a\0refs/heads/main\0ref\0refs/heads/old\0"
        + b"symref-update refs/meta/b\0refs/heads/main\0oid\0"
        + oid.encode("ascii")
        + b"\0"
    )
    assert records[0].old_kind == "ref"
    assert records[0].old_target == "refs/heads/old"
    assert records[1].old_kind == "oid"
    assert records[1].old_oid == oid


def test_installed_cli_nul_mixed_transaction(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)
    payload = (
        b"start\0"
        + b"create refs/heads/release\0"
        + ids["first"].encode("ascii")
        + b"\0"
        + b"symref-create refs/meta/current\0refs/heads/release\0"
        + b"commit\0"
    )
    result = _run(repo, "--stdin", "-z", input_data=payload)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert repo.refs.get_branch("release") == ids["first"]
    assert symbolic_target(repo, "refs/meta/current") == "refs/heads/release"


def test_installed_cli_nul_no_deref_symref_update(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fixture(repo)
    set_symbolic_ref(repo, "refs/meta/current", "refs/heads/main")
    payload = (
        b"option no-deref\0"
        b"symref-update refs/meta/current\0refs/heads/other\0ref\0refs/heads/main\0"
    )
    result = _run(repo, "--stdin", "-z", input_data=payload)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert symbolic_target(repo, "refs/meta/current") == "refs/heads/other"


def test_nul_symref_delete_requires_explicit_optional_field() -> None:
    with pytest.raises(ValueError, match="unexpected end of input"):
        parse_update_records_z(b"symref-delete refs/meta/a\0")
    parsed = parse_update_records_z(b"symref-delete refs/meta/a\0\0")
    assert parsed[0].old_target is None
