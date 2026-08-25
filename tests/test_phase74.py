"""Phase 74 tests: coordinated, fail-closed repository maintenance."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from pygit import Repository, garbage_collect
from pygit.objects import BlobObject, CommitObject, Identity, TreeEntry, TreeObject


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


def _object_path(repo: Repository, oid: str) -> Path:
    return repo.store.root / oid[:2] / oid[2:]


def _age(repo: Repository, *oids: str, seconds: int = 60 * 24 * 60 * 60) -> None:
    stamp = time.time() - seconds
    for oid in oids:
        path = _object_path(repo, oid)
        os.utime(path, (stamp, stamp))


def _record(old: str, new: str, timestamp: int, message: str) -> str:
    return (
        f"{old} {new} Tester <tester@example.com> "
        f"{timestamp} +0000\t{message}\n"
    )


def _head_log(repo: Repository, text: str) -> Path:
    path = repo.pygit_dir / "logs" / "HEAD"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _set_current(repo: Repository, commit: str) -> None:
    repo.refs.set_branch("main", commit)
    repo.refs.set_head_symbolic("main")


def test_gc_expires_reflog_but_preserves_its_history_for_one_gc_cycle(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    old_blob, old_tree, old_commit = _commit(repo, b"old\n", "old")
    new_blob, new_tree, new_commit = _commit(repo, b"new\n", "new")
    _set_current(repo, new_commit)
    log = _head_log(repo, _record(ZERO, old_commit, 1, "recovery-only old commit"))
    _age(repo, old_blob, old_tree, old_commit, new_blob, new_tree, new_commit)

    first = garbage_collect(repo)

    assert first.repack.object_count == 3
    assert first.reflog is not None and first.reflog.expired == 1
    assert log.read_text(encoding="utf-8") == ""
    assert old_commit in first.preserved_expired_roots
    assert first.prune is not None
    assert not ({old_blob, old_tree, old_commit} & set(first.prune.oids))
    for oid in (old_blob, old_tree, old_commit):
        assert repo.store.read(oid).hash() == oid
    for oid in (new_blob, new_tree, new_commit):
        assert repo.store.read(oid).hash() == oid
        assert not _object_path(repo, oid).exists()

    second = garbage_collect(repo)
    assert second.prune is not None
    assert {old_blob, old_tree, old_commit}.issubset(set(second.prune.oids))
    for oid in (old_blob, old_tree, old_commit):
        with pytest.raises(KeyError):
            repo.store.read(oid)
    assert second.final_reachable == 3


def test_recent_reflog_keeps_recovery_only_history(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    old_blob, old_tree, old_commit = _commit(repo, b"old\n", "old")
    _, _, new_commit = _commit(repo, b"new\n", "new")
    _set_current(repo, new_commit)
    _head_log(repo, _record(ZERO, old_commit, int(time.time()), "recent recovery"))
    _age(repo, old_blob, old_tree, old_commit)

    result = garbage_collect(repo, prune_expire_before=time.time())

    assert result.reflog is not None and result.reflog.expired == 0
    assert result.prune is not None
    assert old_commit not in result.prune.oids
    assert repo.store.read(old_commit).hash() == old_commit
    assert repo.store.read(old_tree).hash() == old_tree
    assert repo.store.read(old_blob).hash() == old_blob


def test_no_reflog_expire_preserves_old_recovery_root(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    old_blob, old_tree, old_commit = _commit(repo, b"old\n", "old")
    _, _, current = _commit(repo, b"current\n", "current")
    _set_current(repo, current)
    _head_log(repo, _record(ZERO, old_commit, 1, "keep old recovery"))
    _age(repo, old_blob, old_tree, old_commit)

    result = garbage_collect(
        repo,
        expire_reflogs_enabled=False,
        prune_expire_before=time.time(),
    )

    assert result.reflog is None
    assert result.prune is not None and old_commit not in result.prune.oids
    assert repo.store.read(old_commit).hash() == old_commit


def test_no_prune_api_can_expire_reflog_without_removing_old_objects(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    old_blob, old_tree, old_commit = _commit(repo, b"old\n", "old")
    _, _, current = _commit(repo, b"current\n", "current")
    _set_current(repo, current)
    log = _head_log(repo, _record(ZERO, old_commit, 1, "expire but do not prune"))
    _age(repo, old_blob, old_tree, old_commit)

    result = garbage_collect(repo, prune_objects=False)

    assert result.reflog is not None and result.reflog.expired == 1
    assert result.prune is None
    assert log.read_text(encoding="utf-8") == ""
    assert repo.store.read(old_commit).hash() == old_commit


def test_dry_run_preflights_every_phase_without_mutation(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    old_blob, old_tree, old_commit = _commit(repo, b"old\n", "old")
    new_blob, new_tree, new_commit = _commit(repo, b"new\n", "new")
    _set_current(repo, new_commit)
    log = _head_log(repo, _record(ZERO, old_commit, 1, "would expire"))
    _age(repo, old_blob, old_tree, old_commit, new_blob, new_tree, new_commit)
    before_log = log.read_bytes()
    loose_before = {
        oid: _object_path(repo, oid).read_bytes()
        for oid in (old_blob, old_tree, old_commit, new_blob, new_tree, new_commit)
    }

    result = garbage_collect(repo, dry_run=True)

    assert result.dry_run
    assert result.repack.dry_run
    assert result.reflog is not None and result.reflog.dry_run
    assert result.prune is not None and result.prune.dry_run
    assert old_commit in result.preserved_expired_roots
    assert old_commit not in result.prune.oids
    assert log.read_bytes() == before_log
    for oid, raw in loose_before.items():
        assert _object_path(repo, oid).read_bytes() == raw
    pack_dir = repo.store.root / "pack"
    assert not pack_dir.exists() or not list(pack_dir.glob("*.pack"))


def test_malformed_reflog_aborts_before_repack_mutates_storage(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    blob, tree, commit = _commit(repo, b"current\n", "current")
    _set_current(repo, commit)
    log = _head_log(repo, "not a valid reflog\n")
    before = log.read_bytes()

    with pytest.raises(ValueError, match="malformed reflog"):
        garbage_collect(repo)

    assert log.read_bytes() == before
    assert all(_object_path(repo, oid).exists() for oid in (blob, tree, commit))
    pack_dir = repo.store.root / "pack"
    assert not pack_dir.exists() or not list(pack_dir.glob("*.pack"))


def test_missing_expired_historical_root_aborts_before_any_mutation(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    blob, tree, commit = _commit(repo, b"current\n", "current")
    _set_current(repo, commit)
    missing = "a" * 64
    log = _head_log(repo, _record(ZERO, missing, 1, "missing history"))
    before = log.read_bytes()

    with pytest.raises((KeyError, ValueError, RuntimeError)):
        garbage_collect(repo)

    assert log.read_bytes() == before
    assert all(_object_path(repo, oid).exists() for oid in (blob, tree, commit))
    pack_dir = repo.store.root / "pack"
    assert not pack_dir.exists() or not list(pack_dir.glob("*.pack"))


def test_unhealthy_connectivity_aborts_before_any_maintenance(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    missing = "a" * 64
    tree = repo.store.write(TreeObject([TreeEntry("100644", "missing.txt", missing)]))
    ident = Identity("Tester", "tester@example.com", 1, "+0000")
    commit = repo.store.write(
        CommitObject(tree=tree, parents=[], author=ident, committer=ident, message="broken")
    )
    _set_current(repo, commit)

    with pytest.raises(RuntimeError, match="unhealthy repository"):
        garbage_collect(repo)

    assert _object_path(repo, tree).exists()
    assert _object_path(repo, commit).exists()
    pack_dir = repo.store.root / "pack"
    assert not pack_dir.exists() or not list(pack_dir.glob("*.pack"))


def test_immediate_cutoffs_still_keep_freshly_expired_history_for_this_pass(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    old_blob, old_tree, old_commit = _commit(repo, b"old\n", "old")
    _, _, current = _commit(repo, b"current\n", "current")
    _set_current(repo, current)
    _head_log(repo, _record(ZERO, old_commit, int(time.time()), "fresh but explicitly expired"))
    _age(repo, old_blob, old_tree, old_commit)
    future = time.time() + 5

    first = garbage_collect(
        repo,
        prune_expire_before=future,
        reflog_expire_before=future,
        reflog_unreachable_before=future,
    )

    assert first.reflog is not None and first.reflog.expired == 1
    assert old_commit in first.preserved_expired_roots
    assert first.prune is not None and old_commit not in first.prune.oids
    assert repo.store.read(old_commit).hash() == old_commit

    second = garbage_collect(
        repo,
        prune_expire_before=future,
        expire_reflogs_enabled=False,
    )
    assert second.prune is not None and old_commit in second.prune.oids
    with pytest.raises(KeyError):
        repo.store.read(old_commit)


def test_installed_gc_cli_uses_safe_front_door_and_supports_dry_run(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _, _, commit = _commit(repo, b"cli\n", "cli")
    _set_current(repo, commit)
    before = _object_path(repo, commit).read_bytes()

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pygit",
            "gc",
            "--dry-run",
            "--verbose",
            "--prune=now",
            "--reflog-expire=now",
            "--reflog-expire-unreachable=now",
        ],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Garbage collection: would repack" in result.stdout
    assert "repack\t" in result.stdout
    assert _object_path(repo, commit).read_bytes() == before
    pack_dir = repo.store.root / "pack"
    assert not pack_dir.exists() or not list(pack_dir.glob("*.pack"))


def test_installed_gc_no_prune_preserves_legacy_no_write_behavior(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    old_blob, old_tree, old_commit = _commit(repo, b"old\n", "old")
    _, _, current = _commit(repo, b"current\n", "current")
    _set_current(repo, current)
    log = _head_log(repo, _record(ZERO, old_commit, 1, "legacy no-prune"))
    _age(repo, old_blob, old_tree, old_commit)
    before_log = log.read_bytes()
    before_objects = {
        oid: _object_path(repo, oid).read_bytes()
        for oid in (old_blob, old_tree, old_commit)
    }

    result = subprocess.run(
        [sys.executable, "-m", "pygit", "gc", "--no-prune", "--verbose"],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Garbage collection: would repack" in result.stdout
    assert log.read_bytes() == before_log
    for oid, raw in before_objects.items():
        assert _object_path(repo, oid).read_bytes() == raw
    pack_dir = repo.store.root / "pack"
    assert not pack_dir.exists() or not list(pack_dir.glob("*.pack"))
