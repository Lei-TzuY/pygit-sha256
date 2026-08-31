"""Durable inode-aware release for transaction-owned lockfiles.

This module factors the common release boundary needed by the packfile-URI
publication stack: a transaction may unlink only the pathname that still names
the inode it acquired, and a successful namespace removal is followed by a
parent-directory durability fence on POSIX.

Phase362 adds a batch cleanup boundary for callers that own several lockfiles at
once. Cleanup proceeds in reverse acquisition order and is best-effort across
all owned locks: the first error is preserved and re-raised only after every
remaining ownership descriptor has had a chance to close/release. This avoids
turning one durability failure into stranded sibling locks.

Phase366 makes the directory durability fence resilient to POSIX EINTR. A signal
that interrupts ``fsync(2)`` does not by itself mean the durability operation
failed, so the helper retries ``InterruptedError`` while preserving every other
I/O error as a hard failure.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class OwnedLockIdentity:
    """Descriptor-backed identity for one transaction-owned lockfile."""

    fd: int
    device: int
    inode: int


def _fsync_retry(fd: int) -> None:
    """Retry an interrupted fsync while preserving non-EINTR failures."""

    while True:
        try:
            os.fsync(fd)
            return
        except InterruptedError:
            continue


def fsync_directory(path: Path) -> None:
    """Durably fence one directory namespace on POSIX.

    Python/Windows does not expose the same directory-fd fsync contract used by
    the POSIX implementation, so Windows deliberately keeps the existing atomic
    lock semantics without claiming a power-loss durability guarantee.

    POSIX ``fsync`` may be interrupted by a signal before completion. Such an
    ``InterruptedError`` is retried; all other failures still propagate so a
    caller cannot report durable cleanup without a successful directory fence.
    """

    directory = Path(path)
    if os.name == "nt":
        return

    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    fd = os.open(directory, flags)
    try:
        _fsync_retry(fd)
    finally:
        os.close(fd)


def release_owned_lock_durably(path: Path, ownership: OwnedLockIdentity) -> bool:
    """Release an owned lock without unlinking a replacement pathname.

    The retained descriptor pins the original inode. The live pathname is
    inspected without following symlinks and is removed only when its
    ``(st_dev, st_ino)`` pair still matches the descriptor-derived identity.
    Missing/replaced paths are preserved. If this call removes the pathname, it
    fsyncs the parent directory before reporting success.

    The ownership descriptor is closed on every path, including directory-fsync
    failure. A post-unlink fsync error propagates: the lock may already be gone,
    but the caller must not report durable cleanup success.
    """

    lock = Path(path)
    try:
        try:
            stat_result = os.stat(lock, follow_symlinks=False)
        except FileNotFoundError:
            return False

        if (stat_result.st_dev, stat_result.st_ino) != (
            ownership.device,
            ownership.inode,
        ):
            return False

        try:
            lock.unlink()
        except FileNotFoundError:
            return False
        fsync_directory(lock.parent)
        return True
    finally:
        try:
            os.close(ownership.fd)
        except OSError:
            pass


def release_owned_locks_durably(
    locks: Iterable[tuple[Path, OwnedLockIdentity]],
) -> tuple[Path, ...]:
    """Durably release several owned locks without stranding siblings on error.

    Callers normally acquire several lockfiles in forward order and release them
    in reverse order. Each item keeps the single-lock success-after-durability
    contract: an owned pathname is removed only when its live inode still matches,
    and a successful removal is followed by a parent-directory fsync on POSIX.

    A release or durability failure for one lock does *not* stop cleanup of locks
    that were acquired earlier. The first exception is remembered, every later
    item is still processed (therefore closing every retained descriptor), and the
    original exception is re-raised after cleanup completes. The returned tuple
    contains only pathnames whose owned namespace entries were durably removed.
    """

    items = tuple(locks)
    removed: list[Path] = []
    first_error: BaseException | None = None

    for path, ownership in reversed(items):
        try:
            if release_owned_lock_durably(path, ownership):
                removed.append(Path(path))
        except BaseException as exc:
            if first_error is None:
                first_error = exc

    if first_error is not None:
        raise first_error
    return tuple(removed)
