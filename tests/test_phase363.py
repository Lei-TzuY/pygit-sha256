from __future__ import annotations

import os
from pathlib import Path

import pytest

import pygit.durable_owned_lock as durable_owned_lock
from pygit.durable_owned_lock import OwnedLockIdentity, release_owned_locks_durably


def _owned_lock(path: Path) -> OwnedLockIdentity:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"owned\n")
    fd = os.open(path, os.O_RDONLY)
    os.set_inheritable(fd, False)
    stat_result = os.fstat(fd)
    return OwnedLockIdentity(fd=fd, device=stat_result.st_dev, inode=stat_result.st_ino)


def _fd_is_closed(fd: int) -> bool:
    try:
        os.fstat(fd)
    except OSError:
        return True
    return False


def test_batch_release_coalesces_sibling_directory_fsync(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / ".pygit"
    paths = [root / "HEAD.lock", root / "packed-refs.lock", root / "shallow.lock"]
    ownership = [(path, _owned_lock(path)) for path in paths]
    fenced: list[Path] = []

    monkeypatch.setattr(durable_owned_lock, "fsync_directory", lambda path: fenced.append(Path(path)))

    removed = release_owned_locks_durably(ownership)

    assert removed == tuple(reversed(paths))
    assert fenced == [root]
    assert all(not path.exists() for path in paths)
    assert all(_fd_is_closed(identity.fd) for _, identity in ownership)


def test_batch_release_fences_each_changed_parent_once(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / ".pygit"
    refs = root / "refs" / "heads"
    logs = root / "logs" / "refs" / "heads"
    paths = [
        refs / "main.lock",
        refs / "topic.lock",
        logs / "main.lock",
        logs / "topic.lock",
    ]
    ownership = [(path, _owned_lock(path)) for path in paths]
    fenced: list[Path] = []

    monkeypatch.setattr(durable_owned_lock, "fsync_directory", lambda path: fenced.append(Path(path)))

    removed = release_owned_locks_durably(ownership)

    assert removed == tuple(reversed(paths))
    assert fenced == [logs, refs]
    assert len(fenced) == len(set(fenced)) == 2


def test_batch_release_continues_other_directory_fences_after_failure(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / ".pygit"
    first_parent = root / "refs" / "heads"
    second_parent = root / "logs" / "refs" / "heads"
    first = first_parent / "main.lock"
    second = second_parent / "main.lock"
    first_identity = _owned_lock(first)
    second_identity = _owned_lock(second)
    fenced: list[Path] = []

    def _fence(path: Path) -> None:
        parent = Path(path)
        fenced.append(parent)
        if parent == second_parent:
            raise OSError("injected directory durability failure")

    monkeypatch.setattr(durable_owned_lock, "fsync_directory", _fence)

    with pytest.raises(OSError, match="injected directory durability failure"):
        release_owned_locks_durably(
            [(first, first_identity), (second, second_identity)]
        )

    # Reverse release order makes second_parent the first fence. The failure must
    # not prevent the earlier-acquired sibling directory from being fenced.
    assert fenced == [second_parent, first_parent]
    assert not first.exists()
    assert not second.exists()
    assert _fd_is_closed(first_identity.fd)
    assert _fd_is_closed(second_identity.fd)


def test_replacement_path_is_preserved_and_does_not_trigger_fence(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / ".pygit"
    lock = root / "HEAD.lock"
    ownership = _owned_lock(lock)

    lock.unlink()
    lock.write_bytes(b"replacement\n")
    fenced: list[Path] = []
    monkeypatch.setattr(durable_owned_lock, "fsync_directory", lambda path: fenced.append(Path(path)))

    removed = release_owned_locks_durably([(lock, ownership)])

    assert removed == ()
    assert lock.read_bytes() == b"replacement\n"
    assert fenced == []
    assert _fd_is_closed(ownership.fd)


def test_single_parent_fsync_failure_does_not_report_success(
    tmp_path: Path, monkeypatch
) -> None:
    lock = tmp_path / ".pygit" / "HEAD.lock"
    ownership = _owned_lock(lock)

    def _fail(_path: Path) -> None:
        raise OSError("durability fence failed")

    monkeypatch.setattr(durable_owned_lock, "fsync_directory", _fail)

    with pytest.raises(OSError, match="durability fence failed"):
        release_owned_locks_durably([(lock, ownership)])

    assert not lock.exists()
    assert _fd_is_closed(ownership.fd)
