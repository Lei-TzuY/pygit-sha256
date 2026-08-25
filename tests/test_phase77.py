"""Phase 77 tests: strict, read-only reflog inspection."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

from pygit import Repository
from pygit.reflog_show import format_reflog_entry, show_reflog


ZERO = "0" * 64
ONE = "1" * 64
TWO = "2" * 64
THREE = "3" * 64


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "r"))


def _line(old: str, new: str, timestamp: int, message: str, tz: str = "+0000") -> str:
    return f"{old} {new} Tester <tester@example.com> {timestamp} {tz}\t{message}\n"


def _write_log(repo: Repository, ref: str, text: str) -> Path:
    relative = "HEAD" if ref == "HEAD" else ref
    path = repo.pygit_dir / "logs" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _run(repo: Repository, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _snapshot_logs(repo: Repository) -> dict[str, bytes]:
    root = repo.pygit_dir / "logs"
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def test_legacy_and_explicit_show_forms_keep_default_output(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write_log(
        repo,
        "HEAD",
        _line(ZERO, ONE, 100, "first") + _line(ONE, TWO, 200, "second"),
    )

    legacy = _run(repo, "reflog")
    explicit = _run(repo, "reflog", "show")

    expected = f"{TWO[:12]} HEAD@{{0}}: second\n{ONE[:12]} HEAD@{{1}}: first\n".encode()
    assert legacy.returncode == 0, legacy.stderr.decode()
    assert explicit.returncode == 0, explicit.stderr.decode()
    assert legacy.stdout == expected
    assert explicit.stdout == expected


def test_short_branch_name_normalizes_to_existing_reflog(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write_log(repo, "refs/heads/topic", _line(ZERO, ONE, 100, "branch update"))

    entries = show_reflog(repo, "topic")
    cli = _run(repo, "reflog", "show", "topic")

    assert len(entries) == 1
    assert entries[0].ref == "topic"
    assert entries[0].selector == "topic@{0}"
    assert cli.returncode == 0, cli.stderr.decode()
    assert cli.stdout == f"{ONE[:12]} topic@{{0}}: branch update\n".encode()


def test_all_refs_use_global_timestamp_order_with_local_selectors(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write_log(
        repo,
        "HEAD",
        _line(ZERO, ONE, 100, "head-old") + _line(ONE, THREE, 300, "head-new"),
    )
    _write_log(repo, "refs/heads/main", _line(ZERO, TWO, 200, "branch"))

    entries = show_reflog(repo, all_refs=True)

    assert [(entry.ref, entry.index, entry.timestamp) for entry in entries] == [
        ("HEAD", 0, 300),
        ("refs/heads/main", 0, 200),
        ("HEAD", 1, 100),
    ]


def test_limit_reverse_and_formatting_are_deterministic(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write_log(
        repo,
        "HEAD",
        _line(ZERO, ONE, 100, "one")
        + _line(ONE, TWO, 200, "two")
        + _line(TWO, THREE, 300, "three"),
    )

    newest_two = show_reflog(repo, max_count=2)
    reversed_two = show_reflog(repo, max_count=2, reverse=True)

    assert [entry.new_oid for entry in newest_two] == [THREE, TWO]
    assert [entry.new_oid for entry in reversed_two] == [TWO, THREE]
    assert format_reflog_entry(newest_two[0], "%gD %H %h %o %ct %r %gs %%") == (
        f"HEAD@{{0}} {THREE} {THREE[:12]} {TWO} 300 HEAD three %"
    )
    with pytest.raises(ValueError, match="unsupported reflog format"):
        format_reflog_entry(newest_two[0], "%x")


def test_missing_short_reflog_preserves_empty_legacy_result(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    assert show_reflog(repo, "missing") == ()
    result = _run(repo, "reflog", "missing")
    assert result.returncode == 0
    assert result.stdout == b""


def test_malformed_existing_log_fails_loudly(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    path = _write_log(repo, "HEAD", _line(ZERO, ONE, 100, "ok"))
    path.write_text(
        f"not-an-oid {ONE} Tester <tester@example.com> 100 +0000\tbad\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="malformed reflog object ID"):
        show_reflog(repo)

    result = _run(repo, "reflog", "show")
    assert result.returncode == 1
    assert b"malformed reflog object ID" in result.stderr


def test_malformed_timezone_and_utf8_fail_loudly(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    path = _write_log(repo, "HEAD", _line(ZERO, ONE, 100, "bad tz", tz="UTC"))
    with pytest.raises(ValueError, match="malformed reflog timezone"):
        show_reflog(repo)

    path.write_bytes(b"\xff\xfe\n")
    with pytest.raises(ValueError, match="valid UTF-8"):
        show_reflog(repo)


def test_all_refs_refuses_symlinked_logs(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    outside = tmp_path / "outside.log"
    outside.write_text(_line(ZERO, ONE, 100, "outside"), encoding="utf-8")
    link = repo.pygit_dir / "logs" / "refs" / "heads" / "linked"
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable")

    with pytest.raises(RuntimeError, match="symbolic-link"):
        show_reflog(repo, all_refs=True)


def test_short_ref_does_not_hide_unsafe_symlinked_reflog(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    outside = tmp_path / "outside-short.log"
    outside.write_text(_line(ZERO, ONE, 100, "outside"), encoding="utf-8")
    link = repo.pygit_dir / "logs" / "refs" / "heads" / "linked"
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable")

    with pytest.raises((ValueError, RuntimeError), match="escapes logs|symbolic-link"):
        show_reflog(repo, "linked")

    result = _run(repo, "reflog", "show", "linked")
    assert result.returncode == 1
    assert b"escapes logs" in result.stderr or b"symbolic-link" in result.stderr


def test_show_is_read_only_and_cli_options_match_api(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write_log(
        repo,
        "HEAD",
        _line(ZERO, ONE, 100, "one") + _line(ONE, TWO, 200, "two"),
    )
    _write_log(repo, "refs/heads/main", _line(ZERO, THREE, 150, "branch"))
    before = _snapshot_logs(repo)

    api = show_reflog(repo, all_refs=True, max_count=2)
    cli = _run(
        repo,
        "reflog",
        "show",
        "--all",
        "-n",
        "2",
        "--format",
        "%gD\t%H\t%gs",
    )

    assert cli.returncode == 0, cli.stderr.decode()
    assert cli.stdout.decode().splitlines() == [
        f"{entry.selector}\t{entry.new_oid}\t{entry.message}" for entry in api
    ]
    assert _snapshot_logs(repo) == before
