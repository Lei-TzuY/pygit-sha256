from __future__ import annotations

from pathlib import Path

import pytest

import pygit.durable_owned_lock as durable
import pygit.protocol_v2_packfile_uri_incremental_fetch as incremental
from pygit.repo import Repository


MARKER = b"packfile-uri FETCH_HEAD state guard\n"


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def test_fetch_head_state_guard_fsync_retries_eintr_and_retains_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    real_fsync = durable.os.fsync
    calls = 0

    def interrupt_once(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise InterruptedError("injected FETCH_HEAD state-lock EINTR")
        real_fsync(fd)

    monkeypatch.setattr(durable.os, "fsync", interrupt_once)

    lock = incremental._acquire_fetch_head_state_guard(repo.pygit_dir)
    ownership = incremental._FETCH_HEAD_STATE_GUARD_OWNERSHIP[lock]
    try:
        current = incremental.os.stat(lock, follow_symlinks=False)
        assert calls == 2
        assert lock.read_bytes() == MARKER
        assert (current.st_dev, current.st_ino) == (ownership.device, ownership.inode)
        assert incremental.os.get_inheritable(ownership.fd) is False
    finally:
        incremental._release_fetch_head_state_guard(lock)

    assert lock not in incremental._FETCH_HEAD_STATE_GUARD_OWNERSHIP
    assert not lock.exists()


def test_fetch_head_state_guard_retries_multiple_eintr_before_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    real_fsync = durable.os.fsync
    calls = 0

    def interrupt_twice(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise InterruptedError("injected repeated FETCH_HEAD EINTR")
        real_fsync(fd)

    monkeypatch.setattr(durable.os, "fsync", interrupt_twice)

    lock = incremental._acquire_fetch_head_state_guard(repo.pygit_dir)
    try:
        assert calls == 3
        assert lock.read_bytes() == MARKER
    finally:
        incremental._release_fetch_head_state_guard(lock)


def test_fetch_head_state_guard_non_eintr_fsync_failure_removes_owned_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    error = OSError("injected state-lock durability failure")
    calls = 0

    def fail(fd: int) -> None:
        nonlocal calls
        calls += 1
        raise error

    monkeypatch.setattr(durable.os, "fsync", fail)

    with pytest.raises(OSError) as excinfo:
        incremental._acquire_fetch_head_state_guard(repo.pygit_dir)

    lock = incremental._fetch_head_state_guard_path(repo.pygit_dir)
    assert excinfo.value is error
    assert calls == 1
    assert not lock.exists()
    assert lock not in incremental._FETCH_HEAD_STATE_GUARD_OWNERSHIP


def test_fetch_head_state_guard_dup_failure_closes_setup_fd_and_cleans_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    setup_fd = None
    real_dup = incremental.os.dup

    def fail_dup(fd: int) -> int:
        nonlocal setup_fd
        setup_fd = fd
        raise OSError("injected state-lock dup failure")

    monkeypatch.setattr(incremental.os, "dup", fail_dup)

    with pytest.raises(OSError, match="state-lock dup failure"):
        incremental._acquire_fetch_head_state_guard(repo.pygit_dir)

    assert setup_fd is not None
    with pytest.raises(OSError):
        incremental.os.fstat(setup_fd)
    lock = incremental._fetch_head_state_guard_path(repo.pygit_dir)
    assert not lock.exists()
    assert lock not in incremental._FETCH_HEAD_STATE_GUARD_OWNERSHIP

    monkeypatch.setattr(incremental.os, "dup", real_dup)


def test_fetch_head_state_guard_duplicate_hardening_failure_closes_both_fds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    real_set_inheritable = incremental.os.set_inheritable
    real_dup = incremental.os.dup
    setup_fd = None
    ownership_fd = None
    set_calls = 0

    def capture_dup(fd: int) -> int:
        nonlocal setup_fd, ownership_fd
        setup_fd = fd
        ownership_fd = real_dup(fd)
        return ownership_fd

    def fail_second_hardening(fd: int, inheritable: bool) -> None:
        nonlocal set_calls
        set_calls += 1
        if set_calls == 2:
            raise OSError("injected retained-descriptor hardening failure")
        real_set_inheritable(fd, inheritable)

    monkeypatch.setattr(incremental.os, "dup", capture_dup)
    monkeypatch.setattr(incremental.os, "set_inheritable", fail_second_hardening)

    with pytest.raises(OSError, match="retained-descriptor hardening failure"):
        incremental._acquire_fetch_head_state_guard(repo.pygit_dir)

    assert setup_fd is not None
    assert ownership_fd is not None
    with pytest.raises(OSError):
        incremental.os.fstat(setup_fd)
    with pytest.raises(OSError):
        incremental.os.fstat(ownership_fd)
    lock = incremental._fetch_head_state_guard_path(repo.pygit_dir)
    assert not lock.exists()
    assert lock not in incremental._FETCH_HEAD_STATE_GUARD_OWNERSHIP
