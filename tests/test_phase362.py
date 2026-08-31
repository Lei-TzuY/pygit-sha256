from __future__ import annotations

import os
from pathlib import Path

import pytest

import pygit.durable_owned_lock as durable_owned_lock
from pygit.durable_owned_lock import OwnedLockIdentity, release_owned_locks_durably


def _owned_lock(path: Path) -> OwnedLockIdentity:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    os.set_inheritable(fd, False)
    stat_result = os.fstat(fd)
    return OwnedLockIdentity(fd=fd, device=stat_result.st_dev, inode=stat_result.st_ino)


def test_batch_release_uses_reverse_acquisition_order(tmp_path: Path, monkeypatch) -> None:
    first = tmp_path / "first.lock"
    second = tmp_path / "second.lock"
    first_owner = _owned_lock(first)
    second_owner = _owned_lock(second)
    calls: list[Path] = []
    real_release = durable_owned_lock.release_owned_lock_durably

    def recording_release(path: Path, ownership: OwnedLockIdentity) -> bool:
        calls.append(Path(path))
        return real_release(path, ownership)

    monkeypatch.setattr(durable_owned_lock, "release_owned_lock_durably", recording_release)

    removed = release_owned_locks_durably(
        ((first, first_owner), (second, second_owner))
    )

    assert calls == [second, first]
    assert removed == (second, first)
    assert not first.exists()
    assert not second.exists()


def test_batch_release_continues_after_first_durability_failure(
    tmp_path: Path, monkeypatch
) -> None:
    first = tmp_path / "first.lock"
    second = tmp_path / "second.lock"
    first_owner = _owned_lock(first)
    second_owner = _owned_lock(second)
    real_fsync_directory = durable_owned_lock.fsync_directory
    failed = False

    def flaky_fsync_directory(path: Path) -> None:
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("injected directory fsync failure")
        real_fsync_directory(path)

    monkeypatch.setattr(durable_owned_lock, "fsync_directory", flaky_fsync_directory)

    with pytest.raises(OSError, match="injected directory fsync failure"):
        release_owned_locks_durably(
            ((first, first_owner), (second, second_owner))
        )

    # The failing reverse-first lock was already unlinked, and cleanup still
    # reached the sibling acquired before it rather than stranding that lock.
    assert not first.exists()
    assert not second.exists()
    with pytest.raises(OSError):
        os.fstat(first_owner.fd)
    with pytest.raises(OSError):
        os.fstat(second_owner.fd)


def test_batch_release_preserves_replacement_and_releases_other_owned_lock(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.lock"
    second = tmp_path / "second.lock"
    first_owner = _owned_lock(first)
    second_owner = _owned_lock(second)

    second.unlink()
    second.write_bytes(b"foreign replacement\n")

    removed = release_owned_locks_durably(
        ((first, first_owner), (second, second_owner))
    )

    assert removed == (first,)
    assert second.read_bytes() == b"foreign replacement\n"
    assert not first.exists()


def test_batch_release_missing_path_does_not_block_sibling_cleanup(tmp_path: Path) -> None:
    first = tmp_path / "first.lock"
    second = tmp_path / "second.lock"
    first_owner = _owned_lock(first)
    second_owner = _owned_lock(second)
    second.unlink()

    removed = release_owned_locks_durably(
        ((first, first_owner), (second, second_owner))
    )

    assert removed == (first,)
    assert not first.exists()
    with pytest.raises(OSError):
        os.fstat(first_owner.fd)
    with pytest.raises(OSError):
        os.fstat(second_owner.fd)


def test_batch_release_preserves_first_exception_after_later_failure(
    tmp_path: Path, monkeypatch
) -> None:
    first = tmp_path / "first.lock"
    second = tmp_path / "second.lock"
    first_owner = _owned_lock(first)
    second_owner = _owned_lock(second)
    calls = 0

    def always_fail(_path: Path) -> None:
        nonlocal calls
        calls += 1
        raise OSError(f"fence-{calls}")

    monkeypatch.setattr(durable_owned_lock, "fsync_directory", always_fail)

    with pytest.raises(OSError, match="fence-1"):
        release_owned_locks_durably(
            ((first, first_owner), (second, second_owner))
        )

    assert calls == 2
    assert not first.exists()
    assert not second.exists()
