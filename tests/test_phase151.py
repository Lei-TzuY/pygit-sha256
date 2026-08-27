"""Phase 151 tests: status porcelain v2 and NUL-framed machine output."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pygit import Repository
from pygit.status_cli import porcelain_v2_records


ZERO_OID = "0" * 64


def _write(repo: Repository, path: str, text: str) -> None:
    target = repo.worktree / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _run(repo: Repository, *args: str, text: bool = True):
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
        check=False,
    )


def _base_repo(tmp_path: Path) -> Repository:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, "tracked.txt", "base\n")
    repo.add(["tracked.txt"])
    repo.commit("base")
    return repo


def _conflicted_repo(tmp_path: Path) -> Repository:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, "conflict.txt", "base\n")
    repo.add(["conflict.txt"])
    repo.commit("base")
    repo.branch("side")

    _write(repo, "conflict.txt", "theirs\n")
    repo.add(["conflict.txt"])
    repo.commit("theirs")

    repo.checkout("main")
    _write(repo, "conflict.txt", "ours\n")
    repo.add(["conflict.txt"])
    repo.commit("ours")
    assert repo.merge("side")["status"] == "conflicts"
    return repo


def test_porcelain_v2_ordinary_records_include_modes_and_sha256_oids(tmp_path: Path) -> None:
    repo = _base_repo(tmp_path)
    head = repo.refs.resolve_head()
    assert head is not None
    head_entries = repo._commit_tree_entries(head)
    tracked_oid, tracked_mode = head_entries["tracked.txt"]

    _write(repo, "tracked.txt", "worktree changed\n")
    _write(repo, "added.txt", "added\n")
    repo.add(["added.txt"])
    added_entry = repo.index.get("added.txt", 0)
    assert added_entry is not None
    _write(repo, "loose.txt", "untracked\n")

    records = porcelain_v2_records(repo)

    assert records == [
        (
            f"1 A. N... 000000 {added_entry.mode} {added_entry.mode} "
            f"{ZERO_OID} {added_entry.sha} added.txt"
        ),
        (
            f"1 .M N... {tracked_mode} {tracked_mode} {tracked_mode} "
            f"{tracked_oid} {tracked_oid} tracked.txt"
        ),
        "? loose.txt",
    ]
    assert len(added_entry.sha) == 64
    assert len(tracked_oid) == 64


def test_porcelain_v2_branch_headers_use_sha256_and_upstream_counts(tmp_path: Path) -> None:
    repo = _base_repo(tmp_path)
    base = repo.refs.resolve_head()
    assert base is not None
    repo.refs.set_remote("origin", "main", base)

    _write(repo, "tracked.txt", "ahead\n")
    repo.add(["tracked.txt"])
    head = repo.commit("ahead")

    result = _run(repo, "status", "--porcelain=v2", "--branch")

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[:4] == [
        f"# branch.oid {head}",
        "# branch.head main",
        "# branch.upstream origin/main",
        "# branch.ab +1 -0",
    ]


def test_porcelain_v2_initial_branch_headers(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))

    result = _run(repo, "status", "--porcelain=2", "--branch")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[:2] == [
        "# branch.oid (initial)",
        "# branch.head main",
    ]


def test_porcelain_v2_detached_branch_header(tmp_path: Path) -> None:
    repo = _base_repo(tmp_path)
    head = repo.refs.resolve_head()
    assert head is not None
    repo.refs.set_head_detached(head, message="detach for test")

    result = _run(repo, "status", "--porcelain=v2", "--branch")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[:2] == [
        f"# branch.oid {head}",
        "# branch.head (detached)",
    ]


def test_porcelain_v2_unmerged_record_contains_all_stage_metadata(tmp_path: Path) -> None:
    repo = _conflicted_repo(tmp_path)
    s1 = repo.index.get("conflict.txt", 1)
    s2 = repo.index.get("conflict.txt", 2)
    s3 = repo.index.get("conflict.txt", 3)
    assert s1 is not None and s2 is not None and s3 is not None

    result = _run(repo, "status", "--porcelain=v2")

    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        f"u UU N... {s1.mode} {s2.mode} {s3.mode} 100644 "
        f"{s1.sha} {s2.sha} {s3.sha} conflict.txt\n"
    )


def test_porcelain_v2_missing_conflict_stage_uses_zero_mode_and_oid(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, "gone.txt", "base\n")
    repo.add(["gone.txt"])
    repo.commit("base")
    repo.branch("delete-side")

    repo.rm("gone.txt")
    repo.commit("delete theirs")

    repo.checkout("main")
    _write(repo, "gone.txt", "ours changed\n")
    repo.add(["gone.txt"])
    repo.commit("modify ours")
    assert repo.merge("delete-side")["status"] == "conflicts"

    s1 = repo.index.get("gone.txt", 1)
    s2 = repo.index.get("gone.txt", 2)
    assert s1 is not None and s2 is not None
    assert repo.index.get("gone.txt", 3) is None

    result = _run(repo, "status", "--porcelain=v2")

    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        f"u UD N... {s1.mode} {s2.mode} 000000 100644 "
        f"{s1.sha} {s2.sha} {ZERO_OID} gone.txt\n"
    )


def test_porcelain_v2_untracked_and_ignored_records(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, ".gitignore", "ignored.txt\n")
    _write(repo, "ignored.txt", "ignored\n")
    _write(repo, "loose.txt", "loose\n")

    result = _run(repo, "status", "--porcelain=v2", "--ignored")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "? .gitignore",
        "? loose.txt",
        "! ignored.txt",
    ]


def test_porcelain_v2_non_z_quotes_control_and_utf8_path_bytes(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, "line\nbreak.txt", "x")
    _write(repo, "中文.txt", "x")
    _write(repo, "space name.txt", "x")

    result = _run(repo, "status", "--porcelain=v2")

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert '? "line\\nbreak.txt"' in lines
    assert '? "\\344\\270\\255\\346\\226\\207.txt"' in lines
    assert "? space name.txt" in lines


def test_porcelain_v2_z_uses_nul_for_headers_and_raw_paths(tmp_path: Path) -> None:
    repo = _base_repo(tmp_path)
    head = repo.refs.resolve_head()
    assert head is not None
    _write(repo, "line\nbreak.txt", "x")

    result = _run(repo, "status", "--porcelain=v2", "--branch", "-z", text=False)

    assert result.returncode == 0, result.stderr
    expected_prefix = (
        f"# branch.oid {head}\x00# branch.head main\x00".encode("utf-8")
    )
    assert result.stdout.startswith(expected_prefix)
    assert b"? line\nbreak.txt\x00" in result.stdout
    assert b'"line\\nbreak.txt"' not in result.stdout
    assert result.stdout.endswith(b"\x00")


def test_z_without_explicit_format_implies_porcelain_v1(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, "raw\nname.txt", "x")

    result = _run(repo, "status", "-z", text=False)

    assert result.returncode == 0, result.stderr
    assert result.stdout == b"?? raw\nname.txt\x00"


def test_porcelain_v1_keeps_staged_delete_and_same_path_untracked(tmp_path: Path) -> None:
    repo = _base_repo(tmp_path)
    repo.rm("tracked.txt", cached=True)

    result = _run(repo, "status", "--porcelain=1")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "D  tracked.txt",
        "?? tracked.txt",
    ]

    v2 = _run(repo, "status", "--porcelain=2")
    assert v2.returncode == 0, v2.stderr
    lines = v2.stdout.splitlines()
    assert lines[0].startswith(f"1 D. N... 100644 000000 000000 ")
    assert lines[0].endswith(f" {ZERO_OID} tracked.txt")
    assert lines[1] == "? tracked.txt"
