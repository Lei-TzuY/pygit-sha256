from __future__ import annotations

import os
from pathlib import Path

import pytest

import pygit.protocol_v2_packfile_uri_transaction as phase351
from pygit.repo import Repository


MARKER = b"packfile-uri publication guard\n"


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def test_short_write_is_completed_before_fsync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    lock = repo.pygit_dir / "short-write.lock"
    real_write = phase351.os.write
    real_fsync = phase351.os.fsync
    written_total = 0
    write_calls = 0
    fsync_seen = False

    def short_first_write(fd: int, data) -> int:
        nonlocal written_total, write_calls
        write_calls += 1
        payload = bytes(data)
        if write_calls == 1:
            payload = payload[:5]
        count = real_write(fd, payload)
        written_total += count
        return count

    def assert_complete_then_fsync(fd: int) -> None:
        nonlocal fsync_seen
        assert written_total == len(MARKER)
        fsync_seen = True
        real_fsync(fd)

    monkeypatch.setattr(phase351.os, "write", short_first_write)
    monkeypatch.setattr(phase351.os, "fsync", assert_complete_then_fsync)

    phase351._initialize_publication_guard_lock(lock)
    try:
        assert write_calls >= 2
        assert fsync_seen is True
        assert lock.read_bytes() == MARKER
    finally:
        lock.unlink()


def test_interrupted_write_is_retried_without_partial_acceptance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    lock = repo.pygit_dir / "interrupted-write.lock"
    real_write = phase351.os.write
    calls = 0

    def interrupt_once(fd: int, data) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise InterruptedError("injected EINTR")
        return real_write(fd, data)

    monkeypatch.setattr(phase351.os, "write", interrupt_once)

    phase351._initialize_publication_guard_lock(lock)
    try:
        assert calls >= 2
        assert lock.read_bytes() == MARKER
    finally:
        lock.unlink()


def test_zero_progress_write_fails_closed_and_removes_owned_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    lock = repo.pygit_dir / "zero-write.lock"
    fsync_called = False

    monkeypatch.setattr(phase351.os, "write", lambda fd, data: 0)

    def unexpected_fsync(fd: int) -> None:
        nonlocal fsync_called
        fsync_called = True

    monkeypatch.setattr(phase351.os, "fsync", unexpected_fsync)

    with pytest.raises(OSError, match="marker write made no progress"):
        phase351._initialize_publication_guard_lock(lock)

    assert fsync_called is False
    assert not lock.exists()


def test_acquire_guard_set_survives_repeated_short_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    real_write = phase351.os.write
    calls = 0

    def tiny_writes(fd: int, data) -> int:
        nonlocal calls
        calls += 1
        payload = bytes(data)
        return real_write(fd, payload[:3])

    monkeypatch.setattr(phase351.os, "write", tiny_writes)

    acquired = phase351._acquire_publication_guard_locks(repo)
    try:
        assert calls > len(acquired)
        assert acquired
        for lock in acquired:
            assert lock.read_bytes() == MARKER
    finally:
        phase351._release_publication_guard_locks(acquired)

    assert all(not path.exists() for path in phase351._publication_guard_lock_paths(repo))


def test_marker_helper_rejects_negative_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(phase351.os, "write", lambda fd, data: -1)

    with pytest.raises(OSError, match="marker write made no progress"):
        phase351._write_publication_guard_marker(123)
