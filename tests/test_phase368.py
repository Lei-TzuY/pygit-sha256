from __future__ import annotations

from pathlib import Path

import pytest

import pygit.durable_owned_lock as durable
import pygit.protocol_v2_packfile_uri_refs as refs
import pygit.protocol_v2_packfile_uri_transaction as transaction
from pygit.repo import Repository


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def test_publication_guard_fsync_retries_eintr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    lock = repo.pygit_dir / "phase368-publication.lock"
    real_fsync = durable.os.fsync
    calls = 0

    def interrupt_once(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise InterruptedError("injected publication-guard EINTR")
        real_fsync(fd)

    monkeypatch.setattr(durable.os, "fsync", interrupt_once)

    transaction._initialize_publication_guard_lock(lock)
    try:
        assert calls == 2
        assert lock.read_bytes() == b"packfile-uri publication guard\n"
    finally:
        lock.unlink()


def test_owned_publication_guard_retains_identity_after_eintr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    lock = repo.pygit_dir / "phase368-owned-publication.lock"
    real_fsync = durable.os.fsync
    calls = 0

    def interrupt_once(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise InterruptedError("injected owned-publication EINTR")
        real_fsync(fd)

    monkeypatch.setattr(durable.os, "fsync", interrupt_once)
    ownership = transaction._initialize_owned_publication_guard_lock(lock)
    try:
        current = transaction.os.stat(lock, follow_symlinks=False)
        assert calls == 2
        assert (current.st_dev, current.st_ino) == (ownership.device, ownership.inode)
        assert transaction.os.get_inheritable(ownership.fd) is False
    finally:
        transaction.os.close(ownership.fd)
        lock.unlink()


def test_ref_lock_fsync_retries_eintr_and_retains_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    lock = repo.pygit_dir / "refs" / "remotes" / "origin" / "main.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    real_fsync = durable.os.fsync
    calls = 0

    def interrupt_once(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise InterruptedError("injected ref-lock EINTR")
        real_fsync(fd)

    monkeypatch.setattr(durable.os, "fsync", interrupt_once)
    ownership = refs._initialize_ref_lock(lock)
    try:
        current = refs.os.stat(lock, follow_symlinks=False)
        assert calls == 2
        assert lock.read_bytes() == b"packfile-uri ref transaction\n"
        assert (current.st_dev, current.st_ino) == (ownership.device, ownership.inode)
        assert refs.os.get_inheritable(ownership.fd) is False
    finally:
        refs.os.close(ownership.fd)
        lock.unlink()


def test_non_eintr_publication_guard_fsync_failure_still_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    lock = repo.pygit_dir / "phase368-publication-error.lock"
    calls = 0

    def fail(fd: int) -> None:
        nonlocal calls
        calls += 1
        raise OSError("injected durable failure")

    monkeypatch.setattr(durable.os, "fsync", fail)

    with pytest.raises(OSError, match="injected durable failure"):
        transaction._initialize_publication_guard_lock(lock)

    assert calls == 1
    assert not lock.exists()


def test_non_eintr_ref_lock_fsync_failure_removes_only_owned_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    lock = repo.pygit_dir / "refs" / "heads" / "phase368.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    calls = 0

    def fail(fd: int) -> None:
        nonlocal calls
        calls += 1
        raise OSError("injected ref durability failure")

    monkeypatch.setattr(durable.os, "fsync", fail)

    with pytest.raises(OSError, match="injected ref durability failure"):
        refs._initialize_ref_lock(lock)

    assert calls == 1
    assert not lock.exists()
