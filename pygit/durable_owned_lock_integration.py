"""Install Phase364 shared durable-owned-lock release boundaries.

Phase361-363 provide the reusable inode-aware, success-after-directory-fsync
release primitives.  This module binds those primitives to the three retained
ownership registries used by the protocol-v2 packfile-URI publication stack
without changing their established Path-shaped acquire/release caller seams.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

from .durable_owned_lock import (
    OwnedLockIdentity,
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
    """Bind all packfile-URI retained lock registries to the shared primitive."""

    global _INSTALLED
    if _INSTALLED:
        return

    from . import protocol_v2_packfile_uri_incremental_fetch as incremental
    from . import protocol_v2_packfile_uri_refs as refs
    from . import protocol_v2_packfile_uri_transaction as transaction

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

    transaction._release_publication_guard_locks = release_publication_guard_locks
    incremental._release_publication_guard_locks = release_publication_guard_locks
    incremental._release_fetch_head_state_guard = release_fetch_head_state_guard
    refs._release_locks = release_ref_locks
    _INSTALLED = True
