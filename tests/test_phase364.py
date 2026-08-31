from __future__ import annotations

import os
from pathlib import Path

import pygit.durable_owned_lock_integration as integration
import pygit.protocol_v2_packfile_uri_incremental_fetch as incremental
import pygit.protocol_v2_packfile_uri_refs as refs
import pygit.protocol_v2_packfile_uri_transaction as transaction


def _owned_file(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    os.set_inheritable(fd, False)
    st = os.fstat(fd)
    return fd, st.st_dev, st.st_ino


def test_phase364_installs_shared_release_boundaries():
    assert transaction._release_publication_guard_locks.__module__ == integration.__name__
    assert incremental._release_publication_guard_locks is transaction._release_publication_guard_locks
    assert incremental._release_fetch_head_state_guard.__module__ == integration.__name__
    assert refs._release_locks.__module__ == integration.__name__


def test_phase364_repository_guard_release_uses_shared_durable_batch(tmp_path):
    lock = tmp_path / "HEAD.lock"
    fd, device, inode = _owned_file(lock)
    transaction._PUBLICATION_GUARD_OWNERSHIP[lock] = transaction._PublicationGuardOwnership(
        fd=fd, device=device, inode=inode
    )

    transaction._release_publication_guard_locks([lock])

    assert not lock.exists()
    assert lock not in transaction._PUBLICATION_GUARD_OWNERSHIP
    try:
        os.fstat(fd)
    except OSError:
        pass
    else:
        raise AssertionError("retained repository-guard descriptor remained open")


def test_phase364_fetch_head_state_release_preserves_replacement(tmp_path):
    lock = tmp_path / "FETCH_HEAD.state.lock"
    fd, device, inode = _owned_file(lock)
    incremental._FETCH_HEAD_STATE_GUARD_OWNERSHIP[lock] = incremental._FetchHeadStateGuardOwnership(
        fd=fd, device=device, inode=inode
    )

    lock.unlink()
    lock.write_bytes(b"foreign replacement\n")
    incremental._release_fetch_head_state_guard(lock)

    assert lock.read_bytes() == b"foreign replacement\n"
    assert lock not in incremental._FETCH_HEAD_STATE_GUARD_OWNERSHIP
    try:
        os.fstat(fd)
    except OSError:
        pass
    else:
        raise AssertionError("retained FETCH_HEAD-state descriptor remained open")


def test_phase364_target_ref_release_uses_shared_durable_batch(tmp_path):
    first = tmp_path / "refs" / "remotes" / "origin" / "main.lock"
    second = tmp_path / "refs" / "remotes" / "origin" / "next.lock"
    first_fd, first_device, first_inode = _owned_file(first)
    second_fd, second_device, second_inode = _owned_file(second)
    refs._REF_LOCK_OWNERSHIP[first] = refs._RefLockOwnership(
        fd=first_fd, device=first_device, inode=first_inode
    )
    refs._REF_LOCK_OWNERSHIP[second] = refs._RefLockOwnership(
        fd=second_fd, device=second_device, inode=second_inode
    )

    refs._release_locks([first, second])

    assert not first.exists()
    assert not second.exists()
    assert first not in refs._REF_LOCK_OWNERSHIP
    assert second not in refs._REF_LOCK_OWNERSHIP
    for fd in (first_fd, second_fd):
        try:
            os.fstat(fd)
        except OSError:
            pass
        else:
            raise AssertionError("retained target-ref descriptor remained open")
