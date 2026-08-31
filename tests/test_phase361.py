from __future__ import annotations

import os
from pathlib import Path

import pytest

from pygit import durable_owned_lock
from pygit.durable_owned_lock import OwnedLockIdentity, release_owned_lock_durably


def _owned_lock(path: Path) -> OwnedLockIdentity:
    fd = os.open(path, os.O_RDONLY)
    os.set_inheritable(fd, False)
    stat_result = os.fstat(fd)
    return OwnedLockIdentity(fd=fd, device=stat_result.st_dev, inode=stat_result.st_ino)


def test_release_owned_lock_unlinks_matching_inode_and_fsyncs_parent(tmp_path, monkeypatch):
    lock = tmp_path / "guard.lock"
    lock.write_bytes(b"owned\n")
    ownership = _owned_lock(lock)
    calls: list[Path] = []
    monkeypatch.setattr(durable_owned_lock, "fsync_directory", lambda path: calls.append(Path(path)))

    assert release_owned_lock_durably(lock, ownership) is True
    assert not lock.exists()
    assert calls == [tmp_path]
    with pytest.raises(OSError):
        os.fstat(ownership.fd)


def test_release_owned_lock_preserves_replacement_inode(tmp_path, monkeypatch):
    lock = tmp_path / "guard.lock"
    lock.write_bytes(b"owned\n")
    ownership = _owned_lock(lock)
    lock.unlink()
    lock.write_bytes(b"replacement\n")
    calls: list[Path] = []
    monkeypatch.setattr(durable_owned_lock, "fsync_directory", lambda path: calls.append(Path(path)))

    assert release_owned_lock_durably(lock, ownership) is False
    assert lock.read_bytes() == b"replacement\n"
    assert calls == []
    with pytest.raises(OSError):
        os.fstat(ownership.fd)


def test_release_owned_lock_missing_path_only_closes_descriptor(tmp_path, monkeypatch):
    lock = tmp_path / "guard.lock"
    lock.write_bytes(b"owned\n")
    ownership = _owned_lock(lock)
    lock.unlink()
    calls: list[Path] = []
    monkeypatch.setattr(durable_owned_lock, "fsync_directory", lambda path: calls.append(Path(path)))

    assert release_owned_lock_durably(lock, ownership) is False
    assert calls == []
    with pytest.raises(OSError):
        os.fstat(ownership.fd)


def test_release_owned_lock_propagates_directory_fsync_failure_after_unlink(tmp_path, monkeypatch):
    lock = tmp_path / "guard.lock"
    lock.write_bytes(b"owned\n")
    ownership = _owned_lock(lock)

    def fail(_path: Path) -> None:
        raise OSError("directory fsync failed")

    monkeypatch.setattr(durable_owned_lock, "fsync_directory", fail)
    with pytest.raises(OSError, match="directory fsync failed"):
        release_owned_lock_durably(lock, ownership)

    assert not lock.exists()
    with pytest.raises(OSError):
        os.fstat(ownership.fd)


def test_release_owned_lock_identical_bytes_replacement_is_not_ownership(tmp_path, monkeypatch):
    lock = tmp_path / "guard.lock"
    lock.write_bytes(b"same marker\n")
    ownership = _owned_lock(lock)
    lock.unlink()
    lock.write_bytes(b"same marker\n")
    monkeypatch.setattr(
        durable_owned_lock,
        "fsync_directory",
        lambda path: pytest.fail("replacement path must not be fsynced as an owned removal"),
    )

    assert release_owned_lock_durably(lock, ownership) is False
    assert lock.read_bytes() == b"same marker\n"


def test_fsync_directory_closes_descriptor_when_fsync_fails(tmp_path, monkeypatch):
    if os.name == "nt":
        pytest.skip("Windows intentionally has no POSIX directory-fsync contract")

    real_close = os.close
    opened: list[int] = []
    closed: list[int] = []
    real_open = os.open

    def track_open(path, flags, *args):
        fd = real_open(path, flags, *args)
        opened.append(fd)
        return fd

    def track_close(fd: int) -> None:
        closed.append(fd)
        real_close(fd)

    monkeypatch.setattr(durable_owned_lock.os, "open", track_open)
    monkeypatch.setattr(durable_owned_lock.os, "close", track_close)
    monkeypatch.setattr(
        durable_owned_lock.os,
        "fsync",
        lambda fd: (_ for _ in ()).throw(OSError("boom")),
    )

    with pytest.raises(OSError, match="boom"):
        durable_owned_lock.fsync_directory(tmp_path)

    assert len(opened) == 1
    assert closed == opened
