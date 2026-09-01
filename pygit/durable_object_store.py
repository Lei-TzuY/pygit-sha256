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

Phase376 closes the corresponding new-publication race. The temporary file's
inode is pinned before atomic replacement and its descriptor is retained across
the namespace fences. ``write()`` reports success only if the live object path
still names that exact fsynced inode after both fences. If another writer wins a
replacement race, the visible candidate is certified or publication is retried.
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


def _publish_new_loose_object(
    path: Path,
    sha: str,
    compressed: bytes,
    objects_root: Path,
) -> bool:
    """Publish one new loose-object inode and durably certify its namespace.

    The temporary descriptor remains open after its contents are flushed and
    fsynced. Its `(st_dev, st_ino)` identity is captured before `os.replace()`.
    After the fanout and object-root namespace fences complete, success requires
    the live object pathname to still name that exact inode. A competing writer
    that replaced the pathname therefore causes a `False` result rather than a
    false durability claim.
    """

    fanout = path.parent
    fanout.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".tmp-{sha}-", dir=str(fanout))
    temp_path = Path(temp_name)
    try:
        os.set_inheritable(fd, False)
        with os.fdopen(fd, "wb", closefd=False) as handle:
            handle.write(compressed)
            handle.flush()
            _fsync_retry(fd)
        pinned = os.fstat(fd)
        os.replace(temp_path, path)
        fsync_directory(fanout)
        fsync_directory(objects_root)
        return _path_still_names_inode(path, pinned.st_dev, pinned.st_ino)
    finally:
        try:
            os.close(fd)
        finally:
            if temp_path.exists():
                temp_path.unlink()


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

        compressed = zlib.compress(store_bytes)
        while True:
            if _publish_new_loose_object(obj_path, sha, compressed, self.root):
                return sha

            # Another writer replaced our fully durable inode before the final
            # namespace correlation. Accept that winner only after independently
            # certifying its content, inode, and durability. Otherwise retry our
            # normal same-directory atomic publication.
            if _certify_existing_loose_object(obj_path, sha, self.root):
                return sha

    ObjectStore.write = write
    _INSTALLED = True
