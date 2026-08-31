"""Durable inode-aware release for transaction-owned lockfiles.

This module factors the common release boundary needed by the packfile-URI
publication stack: a transaction may unlink only the pathname that still names
the inode it acquired, and a successful namespace removal is followed by a
parent-directory durability fence on POSIX.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


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


def release_owned_lock_durably(path: Path, ownership: OwnedLockIdentity) -> bool:
    """Release an owned lock without unlinking a replacement pathname.

    The retained descriptor pins the original inode.  The live pathname is
    inspected without following symlinks and is removed only when its
    ``(st_dev, st_ino)`` pair still matches the descriptor-derived identity.
    Missing/replaced paths are preserved.  If this call removes the pathname, it
    fsyncs the parent directory before reporting success.

    The ownership descriptor is closed on every path, including directory-fsync
    failure.  A post-unlink fsync error propagates: the lock may already be gone,
    but the caller must not report durable cleanup success.
    """

    lock = Path(path)
    removed = False
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
        removed = True
        fsync_directory(lock.parent)
        return True
    finally:
        try:
            os.close(ownership.fd)
        except OSError:
            pass
