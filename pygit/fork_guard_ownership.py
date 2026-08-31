"""Fork-safety for process-local publication guard ownership registries.

The packfile-URI publication path retains non-inheritable descriptors so lock
cleanup can prove pathname/inode ownership. ``FD_CLOEXEC`` protects an ``exec``
boundary, but a plain ``fork`` still copies both the descriptors and the Python
registries into the child. Without an explicit child-side reset, the child could
later run a release helper and unlink a lock that is still owned by the parent.

This module registers one ``after_in_child`` hook where supported. The child
closes only its inherited descriptor copies and clears only its inherited
registry copies. It never unlinks a lock pathname. The parent keeps its original
registries/descriptors and remains the sole process allowed to release those
locks through the normal inode-aware cleanup paths.
"""

from __future__ import annotations

import os
import sys
from typing import MutableMapping


_REGISTRY_SPECS = (
    (
        "pygit.protocol_v2_packfile_uri_transaction",
        "_PUBLICATION_GUARD_OWNERSHIP",
    ),
    (
        "pygit.protocol_v2_packfile_uri_incremental_fetch",
        "_FETCH_HEAD_STATE_GUARD_OWNERSHIP",
    ),
)
_REGISTERED = False


def _close_and_clear_registry(registry: MutableMapping) -> None:
    """Close inherited ownership fds and discard the child registry copy."""

    for ownership in tuple(registry.values()):
        fd = getattr(ownership, "fd", None)
        if isinstance(fd, int):
            try:
                os.close(fd)
            except OSError:
                pass
    registry.clear()


def _discard_inherited_guard_ownership() -> None:
    """Run in a fork child without importing new publication modules."""

    for module_name, registry_name in _REGISTRY_SPECS:
        module = sys.modules.get(module_name)
        if module is None:
            continue
        registry = getattr(module, registry_name, None)
        if isinstance(registry, MutableMapping):
            _close_and_clear_registry(registry)


def install_fork_guard_ownership_cleanup() -> bool:
    """Register the child cleanup once; return whether fork hooks are available."""

    global _REGISTERED
    register_at_fork = getattr(os, "register_at_fork", None)
    if register_at_fork is None:
        return False
    if not _REGISTERED:
        register_at_fork(after_in_child=_discard_inherited_guard_ownership)
        _REGISTERED = True
    return True
