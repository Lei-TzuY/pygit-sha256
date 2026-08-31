from __future__ import annotations

import os
from pathlib import Path

import pytest

import pygit.protocol_v2_packfile_uri_incremental_fetch as phase356
from pygit.repo import Repository


FOREIGN = b"foreign FETCH_HEAD state guard replacement\n"


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def test_acquired_state_guard_retains_non_inheritable_identity_fd(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    lock = phase356._acquire_fetch_head_state_guard(repo.pygit_dir)
    ownership = phase356._FETCH_HEAD_STATE_GUARD_OWNERSHIP[lock]
    try:
        assert os.get_inheritable(ownership.fd) is False
        fd_stat = os.fstat(ownership.fd)
        path_stat = os.stat(lock, follow_symlinks=False)
        assert (fd_stat.st_dev, fd_stat.st_ino) == (
            ownership.device,
            ownership.inode,
        )
        assert (path_stat.st_dev, path_stat.st_ino) == (
            ownership.device,
            ownership.inode,
        )
    finally:
        phase356._release_fetch_head_state_guard(lock)

    assert lock not in phase356._FETCH_HEAD_STATE_GUARD_OWNERSHIP
    with pytest.raises(OSError):
        os.fstat(ownership.fd)


def test_release_removes_only_the_owned_state_guard_inode(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    lock = phase356._acquire_fetch_head_state_guard(repo.pygit_dir)
    ownership = phase356._FETCH_HEAD_STATE_GUARD_OWNERSHIP[lock]

    phase356._release_fetch_head_state_guard(lock)

    assert not lock.exists()
    assert lock not in phase356._FETCH_HEAD_STATE_GUARD_OWNERSHIP
    with pytest.raises(OSError):
        os.fstat(ownership.fd)


def test_release_preserves_recreated_foreign_state_guard(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    lock = phase356._acquire_fetch_head_state_guard(repo.pygit_dir)
    ownership = phase356._FETCH_HEAD_STATE_GUARD_OWNERSHIP[lock]

    lock.unlink()
    lock.write_bytes(FOREIGN)
    replacement = os.stat(lock, follow_symlinks=False)
    assert (replacement.st_dev, replacement.st_ino) != (
        ownership.device,
        ownership.inode,
    )

    phase356._release_fetch_head_state_guard(lock)

    assert lock.read_bytes() == FOREIGN
    assert lock not in phase356._FETCH_HEAD_STATE_GUARD_OWNERSHIP
    with pytest.raises(OSError):
        os.fstat(ownership.fd)


def test_release_preserves_recreated_state_guard_with_canonical_marker(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    lock = phase356._acquire_fetch_head_state_guard(repo.pygit_dir)
    ownership = phase356._FETCH_HEAD_STATE_GUARD_OWNERSHIP[lock]

    lock.unlink()
    lock.write_bytes(phase356._FETCH_HEAD_STATE_GUARD_MARKER)
    replacement = os.stat(lock, follow_symlinks=False)
    assert (replacement.st_dev, replacement.st_ino) != (
        ownership.device,
        ownership.inode,
    )

    phase356._release_fetch_head_state_guard(lock)

    assert lock.read_bytes() == phase356._FETCH_HEAD_STATE_GUARD_MARKER
    with pytest.raises(OSError):
        os.fstat(ownership.fd)


def test_release_missing_state_guard_closes_retained_descriptor(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    lock = phase356._acquire_fetch_head_state_guard(repo.pygit_dir)
    ownership = phase356._FETCH_HEAD_STATE_GUARD_OWNERSHIP[lock]

    lock.unlink()
    phase356._release_fetch_head_state_guard(lock)

    assert not lock.exists()
    assert lock not in phase356._FETCH_HEAD_STATE_GUARD_OWNERSHIP
    with pytest.raises(OSError):
        os.fstat(ownership.fd)


def test_release_without_ownership_never_unlinks_foreign_path(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    lock = phase356._fetch_head_state_guard_path(repo.pygit_dir)
    lock.write_bytes(FOREIGN)

    phase356._release_fetch_head_state_guard(lock)

    assert lock.read_bytes() == FOREIGN


def test_same_process_cannot_reacquire_removed_but_still_owned_path(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    lock = phase356._acquire_fetch_head_state_guard(repo.pygit_dir)
    ownership = phase356._FETCH_HEAD_STATE_GUARD_OWNERSHIP[lock]
    lock.unlink()
    try:
        with pytest.raises(RuntimeError, match="FETCH_HEAD state.*already exists"):
            phase356._acquire_fetch_head_state_guard(repo.pygit_dir)
        assert lock in phase356._FETCH_HEAD_STATE_GUARD_OWNERSHIP
        assert os.fstat(ownership.fd).st_ino == ownership.inode
    finally:
        phase356._release_fetch_head_state_guard(lock)

    assert not phase356._FETCH_HEAD_STATE_GUARD_OWNERSHIP


def test_ownership_duplication_failure_cleans_transaction_owned_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)

    def fail_dup(fd: int) -> int:
        raise OSError("injected state guard ownership duplication failure")

    monkeypatch.setattr(phase356.os, "dup", fail_dup)

    with pytest.raises(OSError, match="ownership duplication failure"):
        phase356._acquire_fetch_head_state_guard(repo.pygit_dir)

    lock = phase356._fetch_head_state_guard_path(repo.pygit_dir)
    assert not lock.exists()
    assert lock not in phase356._FETCH_HEAD_STATE_GUARD_OWNERSHIP
