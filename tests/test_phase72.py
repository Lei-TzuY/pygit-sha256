"""Phase 72 tests: atomic, reachability-aware reflog expiry."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from pygit import Repository, prune
from pygit.objects import BlobObject, CommitObject, Identity, TreeEntry, TreeObject
from pygit.reflog_expire import expire_reflogs


ZERO = "0" * 64


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def _commit(repo: Repository, payload: bytes, message: str) -> tuple[str, str, str]:
    blob = repo.store.write(BlobObject(payload))
    tree = repo.store.write(TreeObject([TreeEntry("100644", "file.txt", blob)]))
    ident = Identity("Tester", "tester@example.com", 1, "+0000")
    commit = repo.store.write(
        CommitObject(
            tree=tree,
            parents=[],
            author=ident,
            committer=ident,
            message=message,
        )
    )
    return blob, tree, commit


def _log_path(repo: Repository, ref: str) -> Path:
    if ref == "HEAD":
        return repo.pygit_dir / "logs" / "HEAD"
    return repo.pygit_dir / "logs" / Path(*ref.split("/"))


def _record(old: str, new: str, timestamp: int, message: str) -> str:
    return (
        f"{old} {new} Tester <tester@example.com> "
        f"{timestamp} +0000\t{message}\n"
    )


def _write_log(repo: Repository, ref: str, records: list[str]) -> Path:
    path = _log_path(repo, ref)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(records), encoding="utf-8")
    return path


def _object_path(repo: Repository, oid: str) -> Path:
    return repo.store.root / oid[:2] / oid[2:]


def _age_object(repo: Repository, oid: str, seconds: int = 60 * 24 * 60 * 60) -> None:
    stamp = time.time() - seconds
    path = _object_path(repo, oid)
    os.utime(path, (stamp, stamp))


def test_general_expiry_removes_old_record_even_when_oid_is_currently_reachable(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _, _, commit = _commit(repo, b"current\n", "current")
    repo.refs.set_branch("main", commit)
    repo.refs.set_head_symbolic("main")
    path = _write_log(repo, "HEAD", [_record(ZERO, commit, 10, "old current")])

    result = expire_reflogs(
        repo,
        expire_before=20,
        expire_unreachable_before=float("-inf"),
    )

    assert result.expired == 1
    assert result.entries[0].reason == "expire"
    assert path.read_text(encoding="utf-8") == ""
    assert repo.refs.resolve_head() == commit


def test_unreachable_cutoff_removes_only_fully_unreachable_records(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _, _, current = _commit(repo, b"current\n", "current")
    _, _, orphan = _commit(repo, b"orphan\n", "orphan")
    repo.refs.set_branch("main", current)
    repo.refs.set_head_symbolic("main")
    path = _write_log(
        repo,
        "HEAD",
        [
            _record(ZERO, orphan, 10, "unreachable"),
            _record(orphan, current, 10, "lands on current"),
        ],
    )

    result = expire_reflogs(
        repo,
        expire_before=float("-inf"),
        expire_unreachable_before=20,
    )

    assert [entry.message for entry in result.entries] == ["unreachable"]
    text = path.read_text(encoding="utf-8")
    assert "unreachable" not in text
    assert "lands on current" in text


def test_dry_run_reports_without_rewriting(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _, _, commit = _commit(repo, b"dry\n", "dry")
    repo.refs.set_branch("main", commit)
    repo.refs.set_head_symbolic("main")
    path = _write_log(repo, "HEAD", [_record(ZERO, commit, 1, "dry")])
    before = path.read_bytes()

    result = expire_reflogs(repo, expire_before=2, dry_run=True)

    assert result.expired == 1
    assert result.dry_run
    assert path.read_bytes() == before


def test_all_logs_and_explicit_full_ref_selection(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _, _, current = _commit(repo, b"current\n", "current")
    repo.refs.set_branch("main", current)
    repo.refs.set_head_symbolic("main")
    head = _write_log(repo, "HEAD", [_record(ZERO, current, 1, "head")])
    branch = _write_log(repo, "refs/heads/main", [_record(ZERO, current, 1, "branch")])

    result = expire_reflogs(repo, all_refs=True, expire_before=2)

    assert result.scanned_logs == 2
    assert result.expired == 2
    assert set(result.rewritten_logs) == {"HEAD", "refs/heads/main"}
    assert head.read_text(encoding="utf-8") == ""
    assert branch.read_text(encoding="utf-8") == ""


def test_malformed_target_log_aborts_before_any_other_rewrite(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _, _, current = _commit(repo, b"current\n", "current")
    repo.refs.set_branch("main", current)
    repo.refs.set_head_symbolic("main")
    good = _write_log(repo, "HEAD", [_record(ZERO, current, 1, "good")])
    bad = _write_log(repo, "refs/heads/main", ["not a reflog\n"])
    before = good.read_bytes()

    with pytest.raises(ValueError, match="malformed reflog"):
        expire_reflogs(repo, all_refs=True, expire_before=2)

    assert good.read_bytes() == before
    assert bad.read_text(encoding="utf-8") == "not a reflog\n"


def test_unhealthy_current_connectivity_aborts_before_rewrite(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    missing = "a" * 64
    repo.refs.set_branch("main", missing)
    repo.refs.set_head_symbolic("main")
    path = _write_log(repo, "HEAD", [_record(ZERO, missing, 1, "bad current")])
    before = path.read_bytes()

    with pytest.raises(RuntimeError, match="unhealthy repository"):
        expire_reflogs(repo, expire_before=2)

    assert path.read_bytes() == before


def test_ref_names_must_be_head_or_fully_qualified(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    with pytest.raises(ValueError, match="fully-qualified"):
        expire_reflogs(repo, ["main"], expire_before=2)
    with pytest.raises(ValueError):
        expire_reflogs(repo, ["refs/heads/../outside"], expire_before=2)


def test_atomic_rewrite_rolls_back_if_second_replace_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path)
    _, _, current = _commit(repo, b"current\n", "current")
    repo.refs.set_branch("main", current)
    repo.refs.set_head_symbolic("main")
    head = _write_log(repo, "HEAD", [_record(ZERO, current, 1, "head")])
    branch = _write_log(repo, "refs/heads/main", [_record(ZERO, current, 1, "branch")])
    originals = {head: head.read_bytes(), branch: branch.read_bytes()}

    import pygit.reflog_expire as module

    real_replace = module.os.replace
    calls = {"count": 0}

    def flaky_replace(src: str, dst: str) -> None:
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("simulated replace failure")
        real_replace(src, dst)

    monkeypatch.setattr(module.os, "replace", flaky_replace)
    with pytest.raises(OSError, match="simulated"):
        expire_reflogs(repo, all_refs=True, expire_before=2)

    assert head.read_bytes() == originals[head]
    assert branch.read_bytes() == originals[branch]


def test_reflog_expire_then_prune_reclaims_recovery_only_history(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    old_blob, old_tree, old_commit = _commit(repo, b"old\n", "old")
    new_blob, new_tree, new_commit = _commit(repo, b"new\n", "new")
    repo.refs.set_branch("main", new_commit)
    repo.refs.set_head_symbolic("main")
    _write_log(
        repo,
        "HEAD",
        [
            _record(ZERO, old_commit, 1, "old"),
            _record(old_commit, new_commit, 2, "move"),
        ],
    )
    for oid in (old_blob, old_tree, old_commit, new_blob, new_tree, new_commit):
        _age_object(repo, oid)

    before = prune(repo, expire_before=time.time(), dry_run=True)
    assert old_commit not in before.oids

    expire_reflogs(repo, expire_before=3)
    after = prune(repo, expire_before=time.time())

    assert {old_blob, old_tree, old_commit}.issubset(set(after.oids))
    assert all(not _object_path(repo, oid).exists() for oid in (old_blob, old_tree, old_commit))
    assert all(_object_path(repo, oid).exists() for oid in (new_blob, new_tree, new_commit))


def test_installed_cli_preserves_legacy_show_and_adds_nested_expire(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _, _, commit = _commit(repo, b"cli\n", "cli")
    repo.refs.set_branch("main", commit)
    repo.refs.set_head_symbolic("main")
    _write_log(repo, "HEAD", [_record(ZERO, commit, 1, "cli-old")])

    show = subprocess.run(
        [sys.executable, "-m", "pygit", "reflog", "HEAD"],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    assert show.returncode == 0, show.stderr
    assert "cli-old" in show.stdout

    expire = subprocess.run(
        [
            sys.executable,
            "-m",
            "pygit",
            "reflog",
            "expire",
            "--expire=now",
            "--expire-unreachable=never",
            "--dry-run",
            "--verbose",
            "HEAD",
        ],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    assert expire.returncode == 0, expire.stderr
    assert "cli-old" in expire.stdout
