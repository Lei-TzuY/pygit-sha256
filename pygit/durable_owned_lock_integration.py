"""Install shared durable-owned-lock and lock-initialization boundaries.

Phase361-362 provide reusable inode-aware, success-after-directory-fsync
release primitives. Phase365 binds those primitives to the three retained
ownership registries used by the protocol-v2 packfile-URI publication stack
without changing their established Path-shaped acquire/release caller seams.

Phase368 also routes the compact repository-publication-guard and target-ref
lock initialization paths through the shared EINTR-safe fsync helper introduced
in Phase366. This keeps transient signal interruption from turning a fully
written lock marker into a spurious acquisition failure while preserving every
non-EINTR error and the existing cleanup/ownership contracts.

Phase369 completes that acquisition-side durability contract for the larger
``FETCH_HEAD.state.lock`` state machine. Its marker fsync now uses the same
shared retry helper without changing the Phase356 retained-descriptor ownership
sequence or the setup-descriptor close contract.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

from .durable_owned_lock import (
    OwnedLockIdentity,
    _fsync_retry,
    release_owned_lock_durably,
    release_owned_locks_durably,
)

_INSTALLED = False


def _identity(ownership) -> OwnedLockIdentity:
    return OwnedLockIdentity(
        fd=ownership.fd,
        device=ownership.device,
        inode=ownership.inode,
    )


def install_durable_owned_lock_release_integration() -> None:
    """Bind packfile-URI retained lock registries to shared durability helpers."""

    global _INSTALLED
    if _INSTALLED:
        return

    from . import protocol_v2_packfile_uri_incremental_fetch as incremental
    from . import protocol_v2_packfile_uri_refs as refs
    from . import protocol_v2_packfile_uri_transaction as transaction

    def initialize_publication_guard_lock(lock: Path) -> None:
        """Initialize one compatibility guard with EINTR-safe marker durability."""

        fd = transaction._open_publication_guard_lock(lock)
        initialized = False
        try:
            transaction._write_publication_guard_marker(fd)
            _fsync_retry(fd)
            transaction.os.close(fd)
            fd = -1
            initialized = True
        finally:
            if fd != -1:
                try:
                    transaction.os.close(fd)
                except OSError:
                    pass
            if not initialized:
                try:
                    Path(lock).unlink()
                except FileNotFoundError:
                    pass

    def initialize_owned_publication_guard_lock(lock: Path):
        """Initialize and retain one publication guard with EINTR-safe fsync."""

        fd = transaction._open_publication_guard_lock(lock)
        initialized = False
        try:
            transaction._write_publication_guard_marker(fd)
            _fsync_retry(fd)
            stat_result = transaction.os.fstat(fd)
            ownership = transaction._PublicationGuardOwnership(
                fd=fd,
                device=stat_result.st_dev,
                inode=stat_result.st_ino,
            )
            initialized = True
            return ownership
        finally:
            if not initialized:
                try:
                    transaction.os.close(fd)
                except OSError:
                    pass
                try:
                    Path(lock).unlink()
                except FileNotFoundError:
                    pass

    def initialize_ref_lock(lock: Path):
        """Initialize and retain one target-ref lock with EINTR-safe fsync."""

        flags = refs.os.O_WRONLY | refs.os.O_CREAT | refs.os.O_EXCL
        if hasattr(refs.os, "O_BINARY"):
            flags |= refs.os.O_BINARY
        if hasattr(refs.os, "O_CLOEXEC"):
            flags |= refs.os.O_CLOEXEC

        fd = refs.os.open(lock, flags, 0o666)
        initialized = False
        try:
            refs.os.set_inheritable(fd, False)
            refs._write_ref_lock_marker(fd)
            _fsync_retry(fd)
            stat_result = refs.os.fstat(fd)
            ownership = refs._RefLockOwnership(
                fd=fd,
                device=stat_result.st_dev,
                inode=stat_result.st_ino,
            )
            initialized = True
            return ownership
        finally:
            if not initialized:
                try:
                    refs.os.close(fd)
                except OSError:
                    pass
                try:
                    Path(lock).unlink()
                except FileNotFoundError:
                    pass

    def acquire_fetch_head_state_guard(pygit_dir: Path) -> Path:
        """Acquire the retained FETCH_HEAD state guard with EINTR-safe fsync."""

        root = Path(pygit_dir)
        root.mkdir(parents=True, exist_ok=True)
        lock = incremental._fetch_head_state_guard_path(root)
        if lock in incremental._FETCH_HEAD_STATE_GUARD_OWNERSHIP:
            raise RuntimeError(
                "cannot lock incremental FETCH_HEAD state: lock file already exists"
            )

        flags = incremental.os.O_WRONLY | incremental.os.O_CREAT | incremental.os.O_EXCL
        if hasattr(incremental.os, "O_BINARY"):
            flags |= incremental.os.O_BINARY
        if hasattr(incremental.os, "O_CLOEXEC"):
            flags |= incremental.os.O_CLOEXEC

        try:
            fd = incremental.os.open(lock, flags, 0o666)
        except FileExistsError as exc:
            raise RuntimeError(
                "cannot lock incremental FETCH_HEAD state: lock file already exists"
            ) from exc

        ownership_fd = -1
        committed = False
        try:
            incremental.os.set_inheritable(fd, False)
            incremental._write_fetch_head_state_guard_marker(fd)
            _fsync_retry(fd)

            ownership_fd = incremental.os.dup(fd)
            incremental.os.set_inheritable(ownership_fd, False)
            stat_result = incremental.os.fstat(ownership_fd)
            ownership = incremental._FetchHeadStateGuardOwnership(
                fd=ownership_fd,
                device=stat_result.st_dev,
                inode=stat_result.st_ino,
            )

            incremental.os.close(fd)
            fd = -1
            incremental._FETCH_HEAD_STATE_GUARD_OWNERSHIP[lock] = ownership
            ownership_fd = -1
            committed = True
        finally:
            if fd != -1:
                try:
                    incremental.os.close(fd)
                except OSError:
                    pass
            if ownership_fd != -1:
                try:
                    incremental.os.close(ownership_fd)
                except OSError:
                    pass
            if not committed:
                try:
                    lock.unlink()
                except FileNotFoundError:
                    pass
        return lock

    def release_publication_guard_locks(locks: Iterable[Path]) -> None:
        owned: list[tuple[Path, OwnedLockIdentity]] = []
        for lock in tuple(locks):
            path = Path(lock)
            ownership = transaction._PUBLICATION_GUARD_OWNERSHIP.pop(path, None)
            if ownership is not None:
                owned.append((path, _identity(ownership)))
        release_owned_locks_durably(owned)

    def release_fetch_head_state_guard(lock: Path) -> None:
        path = Path(lock)
        ownership = incremental._FETCH_HEAD_STATE_GUARD_OWNERSHIP.pop(path, None)
        if ownership is None:
            return
        release_owned_lock_durably(path, _identity(ownership))

    def release_ref_locks(locks: Sequence[Path]) -> None:
        owned: list[tuple[Path, OwnedLockIdentity]] = []
        for lock in tuple(locks):
            path = Path(lock)
            ownership = refs._REF_LOCK_OWNERSHIP.pop(path, None)
            if ownership is not None:
                owned.append((path, _identity(ownership)))
        release_owned_locks_durably(owned)

    transaction._initialize_publication_guard_lock = initialize_publication_guard_lock
    transaction._initialize_owned_publication_guard_lock = initialize_owned_publication_guard_lock
    refs._initialize_ref_lock = initialize_ref_lock
    incremental._acquire_fetch_head_state_guard = acquire_fetch_head_state_guard
    transaction._release_publication_guard_locks = release_publication_guard_locks
    incremental._release_publication_guard_locks = release_publication_guard_locks
    incremental._release_fetch_head_state_guard = release_fetch_head_state_guard
    refs._release_locks = release_ref_locks
    _INSTALLED = True
