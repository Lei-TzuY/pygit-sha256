from __future__ import annotations

import os
from pathlib import Path

import pytest

import pygit.protocol_v2_packfile_uri_incremental_fetch as phase354
from pygit.repo import Repository


MARKER = b"packfile-uri FETCH_HEAD state guard\n"


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def test_state_guard_short_write_completes_before_fsync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    real_write = phase354.os.write
    real_fsync = phase354.os.fsync
    written_total = 0
    write_calls = 0
    fsync_seen = False

    def short_first_write(fd: int, data) -> int:
        nonlocal written_total, write_calls
        write_calls += 1
        payload = bytes(data)
        if write_calls == 1:
            payload = payload[:7]
        count = real_write(fd, payload)
        written_total += count
        return count

    def assert_complete_then_fsync(fd: int) -> None:
        nonlocal fsync_seen
        assert written_total == len(MARKER)
        fsync_seen = True
        real_fsync(fd)

    monkeypatch.setattr(phase354.os, "write", short_first_write)
    monkeypatch.setattr(phase354.os, "fsync", assert_complete_then_fsync)

    lock = phase354._acquire_fetch_head_state_guard(repo.pygit_dir)
    try:
        assert write_calls >= 2
        assert fsync_seen is True
        assert lock.read_bytes() == MARKER
    finally:
        phase354._release_fetch_head_state_guard(lock)


def test_state_guard_retries_interrupted_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    real_write = phase354.os.write
    calls = 0

    def interrupt_once(fd: int, data) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise InterruptedError("injected FETCH_HEAD state guard EINTR")
        return real_write(fd, data)

    monkeypatch.setattr(phase354.os, "write", interrupt_once)

    lock = phase354._acquire_fetch_head_state_guard(repo.pygit_dir)
    try:
        assert calls >= 2
        assert lock.read_bytes() == MARKER
    finally:
        phase354._release_fetch_head_state_guard(lock)


def test_state_guard_zero_progress_fails_closed_without_fsync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    fsync_called = False

    monkeypatch.setattr(phase354.os, "write", lambda fd, data: 0)

    def unexpected_fsync(fd: int) -> None:
        nonlocal fsync_called
        fsync_called = True

    monkeypatch.setattr(phase354.os, "fsync", unexpected_fsync)

    with pytest.raises(OSError, match="state guard marker write made no progress"):
        phase354._acquire_fetch_head_state_guard(repo.pygit_dir)

    assert fsync_called is False
    assert not phase354._fetch_head_state_guard_path(repo.pygit_dir).exists()


def test_state_guard_descriptor_hardening_failure_cleans_owned_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)

    def fail_set_inheritable(fd: int, inheritable: bool) -> None:
        raise OSError("injected FETCH_HEAD descriptor hardening failure")

    monkeypatch.setattr(phase354.os, "set_inheritable", fail_set_inheritable)

    with pytest.raises(OSError, match="descriptor hardening failure"):
        phase354._acquire_fetch_head_state_guard(repo.pygit_dir)

    assert not phase354._fetch_head_state_guard_path(repo.pygit_dir).exists()


def test_state_guard_close_failure_cleans_owned_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    real_close = phase354.os.close
    calls = 0

    def fail_first_close(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected FETCH_HEAD state guard close failure")
        real_close(fd)

    monkeypatch.setattr(phase354.os, "close", fail_first_close)

    with pytest.raises(OSError, match="state guard close failure"):
        phase354._acquire_fetch_head_state_guard(repo.pygit_dir)

    assert calls >= 2
    assert not phase354._fetch_head_state_guard_path(repo.pygit_dir).exists()


def test_state_guard_repeated_tiny_writes_produce_complete_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    real_write = phase354.os.write
    calls = 0

    def tiny_writes(fd: int, data) -> int:
        nonlocal calls
        calls += 1
        payload = bytes(data)
        return real_write(fd, payload[:2])

    monkeypatch.setattr(phase354.os, "write", tiny_writes)

    lock = phase354._acquire_fetch_head_state_guard(repo.pygit_dir)
    try:
        assert calls > 1
        assert lock.read_bytes() == MARKER
    finally:
        phase354._release_fetch_head_state_guard(lock)


def test_state_guard_marker_helper_rejects_negative_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(phase354.os, "write", lambda fd, data: -1)

    with pytest.raises(OSError, match="state guard marker write made no progress"):
        phase354._write_fetch_head_state_guard_marker(123)
