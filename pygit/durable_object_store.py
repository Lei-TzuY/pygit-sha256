"""Crash-safe loose-object publication for the SHA-256 object store.

The core object store has always published loose objects through a same-directory
temporary and atomic replacement. Phase370 completes the new-publication
durability boundary: file fsync retries transient EINTR, every successful object
replacement is followed by POSIX fanout-directory and objects-root fences, and
``write()`` only reports success after those applicable namespace fences complete.

Phase373 closes the existing-object retry gap. A valid loose object is no longer
accepted by a metadata-only fast path: the exact inode that was validated is
fsynced and correlated with the live pathname before and after the fanout/root
namespace fences. If the pathname changes during certification, normal atomic
publication is used instead. This lets a later ``write()`` truthfully recover
from a prior post-replace durability failure instead of silently accepting the
visible-but-not-yet-certified object.
"""

from __future__ import annotations

import errno
import hashlib
import os
import stat
import tempfile
import zlib
from pathlib import Path

from .durable_owned_lock import _fsync_retry, fsync_directory
from .objects.base import GitObject, HASH_ALGO
from .store import ObjectStore


_INSTALLED = False
_READ_CHUNK = 1024 * 1024


def _open_existing_loose_object(path: Path) -> int:
    """Open one candidate loose object without inheriting or following symlinks."""

    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    fd = os.open(path, flags)
    try:
        os.set_inheritable(fd, False)
    except BaseException:
        try:
            os.close(fd)
        finally:
            raise
    return fd


def _read_all_from_fd(fd: int) -> bytes:
    """Read one descriptor to EOF while retrying interrupted reads."""

    chunks: list[bytes] = []
    while True:
        try:
            chunk = os.read(fd, _READ_CHUNK)
        except InterruptedError:
            continue
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _path_still_names_inode(path: Path, device: int, inode: int) -> bool:
    """Return whether *path* still names the exact regular-file inode."""

    try:
        live = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return (
        stat.S_ISREG(live.st_mode)
        and live.st_dev == device
        and live.st_ino == inode
    )


def _certify_existing_loose_object(path: Path, sha: str, objects_root: Path) -> bool:
    """Validate and durably certify an existing loose object.

    The descriptor pins the inode whose compressed payload is hashed. Success is
    reported only after that inode has been fsynced, the pathname still names the
    same inode, and both namespace durability fences have completed. A concurrent
    pathname replacement returns ``False`` so the caller falls back to normal
    atomic publication of the requested content.
    """

    try:
        fd = _open_existing_loose_object(path)
    except FileNotFoundError:
        return False
    except OSError as exc:
        if hasattr(errno, "ELOOP") and exc.errno == errno.ELOOP:
            return False
        raise

    try:
        pinned = os.fstat(fd)
        if not stat.S_ISREG(pinned.st_mode):
            return False

        compressed = _read_all_from_fd(fd)
        try:
            store_bytes = zlib.decompress(compressed)
        except zlib.error:
            return False
        if hashlib.new(HASH_ALGO, store_bytes).hexdigest() != sha:
            return False

        _fsync_retry(fd)
        if not _path_still_names_inode(path, pinned.st_dev, pinned.st_ino):
            return False

        fsync_directory(path.parent)
        fsync_directory(objects_root)
        return _path_still_names_inode(path, pinned.st_dev, pinned.st_ino)
    finally:
        os.close(fd)


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

        if _certify_existing_loose_object(obj_path, sha, self.root):
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
