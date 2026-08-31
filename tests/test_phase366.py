import os
from pathlib import Path

import pytest

from pygit.durable_owned_lock import (
    OwnedLockIdentity,
    _fsync_retry,
    fsync_directory,
    release_owned_lock_durably,
    release_owned_locks_durably,
)


def _owned_lock(path: Path) -> OwnedLockIdentity:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    os.set_inheritable(fd, False)
    stat_result = os.fstat(fd)
    return OwnedLockIdentity(fd=fd, device=stat_result.st_dev, inode=stat_result.st_ino)


def test_fsync_retry_retries_interrupted_error(monkeypatch):
    calls = []

    def fake_fsync(fd):
        calls.append(fd)
        if len(calls) < 3:
            raise InterruptedError("signal")

    monkeypatch.setattr(os, "fsync", fake_fsync)
    _fsync_retry(123)

    assert calls == [123, 123, 123]


def test_fsync_retry_preserves_non_eintr_failure(monkeypatch):
    error = OSError("disk failure")

    def fake_fsync(fd):
        raise error

    monkeypatch.setattr(os, "fsync", fake_fsync)
    with pytest.raises(OSError) as excinfo:
        _fsync_retry(123)

    assert excinfo.value is error


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory-fsync contract")
def test_fsync_directory_retries_eintr_and_closes_fd(tmp_path, monkeypatch):
    real_close = os.close
    fsync_calls = []
    closed = []

    def fake_fsync(fd):
        fsync_calls.append(fd)
        if len(fsync_calls) == 1:
            raise InterruptedError("signal")

    def tracking_close(fd):
        closed.append(fd)
        real_close(fd)

    monkeypatch.setattr(os, "fsync", fake_fsync)
    monkeypatch.setattr(os, "close", tracking_close)

    fsync_directory(tmp_path)

    assert len(fsync_calls) == 2
    assert closed == [fsync_calls[0]]
    assert fsync_calls[0] == fsync_calls[1]


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory-fsync contract")
def test_owned_lock_release_succeeds_after_directory_fsync_eintr(tmp_path, monkeypatch):
    lock = tmp_path / "owned.lock"
    ownership = _owned_lock(lock)
    real_fsync = os.fsync
    calls = 0

    def interrupt_directory_fsync_once(fd):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise InterruptedError("signal")
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", interrupt_directory_fsync_once)

    assert release_owned_lock_durably(lock, ownership) is True
    assert not lock.exists()
    assert calls == 2
    with pytest.raises(OSError):
        os.fstat(ownership.fd)


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory-fsync contract")
def test_batch_release_keeps_phase362_order_with_eintr_retry(tmp_path, monkeypatch):
    first = tmp_path / "first.lock"
    second = tmp_path / "second.lock"
    first_ownership = _owned_lock(first)
    second_ownership = _owned_lock(second)

    calls = []
    interrupted = False

    def fake_fsync(fd):
        nonlocal interrupted
        calls.append(fd)
        if not interrupted:
            interrupted = True
            raise InterruptedError("signal")

    monkeypatch.setattr(os, "fsync", fake_fsync)

    removed = release_owned_locks_durably(
        [(first, first_ownership), (second, second_ownership)]
    )

    assert removed == (second, first)
    assert not first.exists()
    assert not second.exists()
    # Phase362 semantics remain one durability fence per removed lock; only the
    # first fence is retried because it was interrupted.
    assert len(calls) == 3
