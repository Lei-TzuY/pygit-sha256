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

Phase375 makes that existing-object validation fsck-grade and bounded. The zlib
stream is decompressed incrementally into at most one small output chunk at a
time and compared byte-for-byte with the exact Git object envelope being written.
Truncated streams, trailing bytes/concatenated streams, and output beyond the
expected envelope are rejected and repaired through normal atomic publication.

Phase376 closes the corresponding new-publication race. The temporary file's
inode is pinned before atomic replacement and its descriptor is retained across
the namespace fences. ``write()`` reports success only if the live object path
still names that exact fsynced inode after both fences. If another writer wins a
replacement race, the visible candidate is certified through the same strict
Phase375 validator or publication is retried.

Phase380 pins the POSIX fanout and primary ``objects/`` directory inodes as well.
The mature pathname-based directory fence remains as a compatibility seam, but
success additionally requires fsync of retained directory descriptors plus
before/after pathname-to-directory-inode correlation. A namespace rename or
replacement during the durability window therefore forces retry/certification
instead of letting a fence on the wrong directory inode justify success.
"""

from __future__ import annotations

import errno
import hashlib
import os
import stat
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .durable_owned_lock import _fsync_retry, fsync_directory
from .objects.base import GitObject, HASH_ALGO
from .store import ObjectStore


_INSTALLED = False
_READ_CHUNK = 1024 * 1024
_OUTPUT_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class _PinnedDirectory:
    """Retained POSIX identity for one directory durability boundary."""

    fd: int
    path: Path
    device: int
    inode: int


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


def _open_pinned_directory(path: Path) -> Optional[_PinnedDirectory]:
    """Open and pin one directory inode on POSIX; return ``None`` on Windows.

    Windows deliberately keeps the repository's existing atomic publication
    semantics without claiming the POSIX directory-fsync contract that Python
    does not expose there.
    """

    directory = Path(path)
    if os.name == "nt":
        return None

    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    fd = os.open(directory, flags)
    try:
        os.set_inheritable(fd, False)
        pinned = os.fstat(fd)
        if not stat.S_ISDIR(pinned.st_mode):
            raise NotADirectoryError(str(directory))
        return _PinnedDirectory(fd, directory, pinned.st_dev, pinned.st_ino)
    except BaseException:
        try:
            os.close(fd)
        finally:
            raise


def _close_pinned_directory(pinned: Optional[_PinnedDirectory]) -> None:
    if pinned is not None:
        os.close(pinned.fd)


def _path_still_names_directory(pinned: Optional[_PinnedDirectory]) -> bool:
    """Return whether a directory pathname still names its retained inode."""

    if pinned is None:
        return True
    try:
        live = os.stat(pinned.path, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return (
        stat.S_ISDIR(live.st_mode)
        and live.st_dev == pinned.device
        and live.st_ino == pinned.inode
    )


def _fence_pinned_directory(pinned: Optional[_PinnedDirectory], path: Path) -> bool:
    """Fence one namespace and verify that the pathname never changed inode.

    ``fsync_directory(path)`` remains the established compatibility/test seam.
    On POSIX the retained descriptor is the authoritative identity-aware fence;
    pathname replacement before or after that fd fsync makes this attempt fail
    closed so the caller can retry through its normal publication path.
    """

    try:
        fsync_directory(path)
    except FileNotFoundError:
        return False

    if pinned is None:
        return True
    if not _path_still_names_directory(pinned):
        return False
    _fsync_retry(pinned.fd)
    return _path_still_names_directory(pinned)


def _pin_loose_object_directories(
    fanout: Path,
    objects_root: Path,
) -> tuple[Optional[_PinnedDirectory], Optional[_PinnedDirectory]]:
    """Pin primary objects-root then fanout, closing the first on later failure."""

    root_pin = _open_pinned_directory(objects_root)
    try:
        fanout_pin = _open_pinned_directory(fanout)
    except BaseException:
        _close_pinned_directory(root_pin)
        raise
    return fanout_pin, root_pin


def _close_loose_object_directories(
    fanout_pin: Optional[_PinnedDirectory],
    root_pin: Optional[_PinnedDirectory],
) -> None:
    """Close both retained namespace descriptors without stranding the root fd."""

    try:
        _close_pinned_directory(fanout_pin)
    finally:
        _close_pinned_directory(root_pin)


def _read_fd_chunk(fd: int) -> bytes:
    """Read one bounded compressed chunk, retrying interrupted reads."""

    while True:
        try:
            return os.read(fd, _READ_CHUNK)
        except InterruptedError:
            continue


def _matches_exact_zlib_stream(fd: int, expected: bytes) -> bool:
    """Return whether *fd* is exactly one zlib stream for *expected*.

    Output is capped to at most ``_OUTPUT_CHUNK`` bytes per decompressor call and
    never permitted to exceed the expected object envelope by even one byte. A
    valid result requires the zlib end marker, exact byte-for-byte output, and
    physical EOF immediately after that stream. This deliberately rejects the
    loose-object trailing-garbage shape that Git's strict fsck diagnoses as
    corruption even though ordinary object reads may be permissive.
    """

    decompressor = zlib.decompressobj()
    expected_view = memoryview(expected)
    offset = 0

    while True:
        compressed = _read_fd_chunk(fd)
        if not compressed:
            break

        while compressed:
            if decompressor.eof:
                return False

            remaining = len(expected_view) - offset
            max_output = min(_OUTPUT_CHUNK, remaining + 1)
            before = len(compressed)
            try:
                output = decompressor.decompress(compressed, max_output)
            except zlib.error:
                return False

            if len(output) > remaining:
                return False
            if output != expected_view[offset : offset + len(output)]:
                return False
            offset += len(output)

            if decompressor.unused_data:
                return False

            tail = decompressor.unconsumed_tail
            if tail and len(tail) == before and not output:
                # A bounded decoder that cannot consume input or emit output has
                # made no progress; fail closed rather than spin forever.
                return False
            compressed = tail

    return decompressor.eof and offset == len(expected_view)


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


def _certify_existing_loose_object(
    path: Path,
    expected_store_bytes: bytes,
    objects_root: Path,
) -> bool:
    """Validate and durably certify one exact existing loose object.

    The object descriptor pins the inode whose zlib stream is validated. On
    POSIX, retained fanout/root descriptors also pin the namespace inodes being
    fenced. Success requires all three live pathnames to remain correlated with
    those exact inodes through the durability boundary.
    """

    try:
        fd = _open_existing_loose_object(path)
    except FileNotFoundError:
        return False
    except OSError as exc:
        if hasattr(errno, "ELOOP") and exc.errno == errno.ELOOP:
            return False
        raise

    fanout_pin: Optional[_PinnedDirectory] = None
    root_pin: Optional[_PinnedDirectory] = None
    try:
        try:
            fanout_pin, root_pin = _pin_loose_object_directories(path.parent, objects_root)
        except FileNotFoundError:
            return False

        pinned = os.fstat(fd)
        if not stat.S_ISREG(pinned.st_mode):
            return False

        if not _matches_exact_zlib_stream(fd, expected_store_bytes):
            return False

        _fsync_retry(fd)
        if not _path_still_names_inode(path, pinned.st_dev, pinned.st_ino):
            return False
        if not _path_still_names_directory(fanout_pin):
            return False
        if not _path_still_names_directory(root_pin):
            return False

        if not _fence_pinned_directory(fanout_pin, path.parent):
            return False
        if not _fence_pinned_directory(root_pin, objects_root):
            return False

        return (
            _path_still_names_inode(path, pinned.st_dev, pinned.st_ino)
            and _path_still_names_directory(fanout_pin)
            and _path_still_names_directory(root_pin)
        )
    finally:
        try:
            _close_loose_object_directories(fanout_pin, root_pin)
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
    fsynced. POSIX fanout/root directory descriptors are retained across the
    atomic replacement and namespace fences. Success requires both the live
    object path and both directory pathnames to remain on their pinned inodes.
    """

    fanout = path.parent
    fanout.mkdir(parents=True, exist_ok=True)

    try:
        fanout_pin, root_pin = _pin_loose_object_directories(fanout, objects_root)
    except FileNotFoundError:
        return False

    try:
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

            if not _path_still_names_directory(fanout_pin):
                return False
            if not _path_still_names_directory(root_pin):
                return False
            if not _fence_pinned_directory(fanout_pin, fanout):
                return False
            if not _fence_pinned_directory(root_pin, objects_root):
                return False

            return (
                _path_still_names_inode(path, pinned.st_dev, pinned.st_ino)
                and _path_still_names_directory(fanout_pin)
                and _path_still_names_directory(root_pin)
            )
        finally:
            try:
                os.close(fd)
            finally:
                if temp_path.exists():
                    temp_path.unlink()
    finally:
        _close_loose_object_directories(fanout_pin, root_pin)


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

        if _certify_existing_loose_object(obj_path, store_bytes, self.root):
            return sha

        compressed = zlib.compress(store_bytes)
        while True:
            if _publish_new_loose_object(obj_path, sha, compressed, self.root):
                return sha

            # Another writer replaced our fully durable inode or namespace before
            # final correlation. Accept the visible winner only after strict,
            # bounded validation plus object/directory inode-aware durability.
            # Otherwise retry our normal same-directory atomic publication.
            if _certify_existing_loose_object(obj_path, store_bytes, self.root):
                return sha

    ObjectStore.write = write
    _INSTALLED = True
