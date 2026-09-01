"""Crash-safe loose-object publication for the SHA-256 object store.

The core object store has always published loose objects through a same-directory
temporary and atomic replacement. Phase370 completes the durability boundary:
file fsync retries transient EINTR, every successful object replacement is
followed by POSIX fanout-directory and objects-root fences, and ``write()`` only
reports success after those applicable namespace fences complete.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import zlib
from pathlib import Path

from .durable_owned_lock import _fsync_retry, fsync_directory
from .objects.base import GitObject, HASH_ALGO
from .store import ObjectStore


_INSTALLED = False


def install_durable_object_store_support() -> None:
    """Install success-after-durability loose-object publication once."""

    global _INSTALLED
    if _INSTALLED:
        return

    def write(self: ObjectStore, obj: GitObject) -> str:
        """Serialize and durably publish *obj* in the primary SHA-256 store."""

        store_bytes = obj._build_store_bytes()
        sha = hashlib.new(HASH_ALGO, store_bytes).hexdigest()
        obj_path = self._path_for(sha)

        if obj_path.exists() and self._valid_loose_object(obj_path, sha):
            return sha

        fanout = obj_path.parent
        fanout.mkdir(parents=True, exist_ok=True)
        compressed = zlib.compress(store_bytes)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".tmp-{sha}-",
            dir=str(fanout),
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(compressed)
                handle.flush()
                _fsync_retry(handle.fileno())
            os.replace(temp_path, obj_path)
            fsync_directory(fanout)
            # Fence the parent namespace on every successful publication. A
            # visible pre-existing fanout is not proof that a prior attempt's
            # directory-entry fence completed; always fencing objects/ keeps a
            # later success truthful after a previous root-fsync failure.
            fsync_directory(self.root)
        finally:
            if temp_path.exists():
                temp_path.unlink()

        return sha

    ObjectStore.write = write
    _INSTALLED = True
