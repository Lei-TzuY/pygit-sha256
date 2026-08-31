"""Durable inode-aware release for transaction-owned lockfiles.

This module factors the common release boundary needed by the packfile-URI
publication stack: a transaction may unlink only the pathname that still names
the inode it acquired, and a successful namespace removal is followed by a
parent-directory durability fence on POSIX.

Phase362 adds a batch cleanup boundary for callers that own several lockfiles at
once. Cleanup proceeds in reverse acquisition order and is best-effort across all
owned locks: the first error is preserved and re-raised only after every remaining
ownership descriptor has had a chance to close/release.

Phase363 makes the batch boundary directory-aware. All owned pathnames are first
released in reverse acquisition order, then each affected parent directory is
fsynced at most once. This preserves success-after-durability semantics while
avoiding redundant directory fences for sibling lockfiles such as the repository
publication guards that all live directly under ``.pygit``.
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


def fsync_directory(path: Path) -> None:
    """Durably fence one directory namespace on POSIX.

    Python/Windows does not expose the same directory-fd fsync contract used by
    the POSIX implementation, so Windows deliberately keeps the existing atomic
    lock semantics without claiming a power-loss durability guarantee.
    """

    directory = Path(path)
    if os.name == "nt":
        return

    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    fd = os.open(directory, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _unlink_owned_lock(path: Path, ownership: OwnedLockIdentity) -> bool:
    """Remove one still-owned pathname and always close its retained descriptor.

    No directory durability fence is performed here. The helper exists so the
    batch release path can coalesce multiple sibling namespace mutations behind a
    single parent-directory fsync without weakening inode-aware ownership checks.
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
        return True
    finally:
        try:
            os.close(ownership.fd)
        except OSError:
            pass


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
    removed = _unlink_owned_lock(lock, ownership)
    if not removed:
        return False
    fsync_directory(lock.parent)
    return True


def release_owned_locks_durably(
    locks: Iterable[tuple[Path, OwnedLockIdentity]],
) -> tuple[Path, ...]:
    """Durably release several owned locks with one fence per changed directory.

    Pathname ownership is checked in reverse acquisition order. Missing or
    replaced pathnames are preserved, and every retained ownership descriptor is
    closed even if another release fails.

    Successfully unlinked pathnames are grouped by parent directory. After all
    unlink attempts complete, each affected parent is fsynced at most once, in
    first-mutation order. This is sufficient to durably fence all sibling
    removals in that directory and avoids redundant fsyncs for lock groups that
    share a parent.

    Failures remain best-effort. The first unlink or directory-fsync exception is
    preserved, later locks and directories are still processed, and the first
    exception is re-raised only after cleanup is exhausted. The returned tuple
    contains only pathnames whose parent-directory fence completed successfully;
    if the function raises, callers must not infer durable success for removals in
    the failing directory.
    """

    items = tuple(locks)
    removed_by_parent: dict[Path, list[Path]] = {}
    parent_order: list[Path] = []
    first_error: BaseException | None = None

    for path, ownership in reversed(items):
        lock = Path(path)
        try:
            removed = _unlink_owned_lock(lock, ownership)
        except BaseException as exc:
            if first_error is None:
                first_error = exc
            continue

        if not removed:
            continue

        parent = lock.parent
        if parent not in removed_by_parent:
            removed_by_parent[parent] = []
            parent_order.append(parent)
        removed_by_parent[parent].append(lock)

    durably_removed: list[Path] = []
    for parent in parent_order:
        try:
            fsync_directory(parent)
        except BaseException as exc:
            if first_error is None:
                first_error = exc
            continue
        durably_removed.extend(removed_by_parent[parent])

    if first_error is not None:
        raise first_error
    return tuple(durably_removed)
