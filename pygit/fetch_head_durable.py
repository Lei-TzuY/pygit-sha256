"""Crash-safe, serialized FETCH_HEAD replacement for SHA-256-native fetches.

Phase344 added the durability boundary for replace-style FETCH_HEAD writes used
by the mapped incremental packfile-URI path. Phase345 aligns that boundary with
Git's lockfile discipline: one canonical ``FETCH_HEAD.lock`` is acquired with
exclusive creation, populated and fsynced, then atomically renamed to the live
metadata path. Phase346 also makes the lock descriptor explicitly non-inheritable
so child processes cannot accidentally prolong or mutate an in-flight writer.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping, Sequence

from .fetch_head import _description


_HEX = frozenset("0123456789abcdef")


def _fsync_directory(path: Path) -> None:
    """Flush a directory namespace on platforms with POSIX directory fds."""

    if os.name == "nt":
        return

    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _render_fetch_head(
    refs: Mapping[str, str],
    *,
    source: str,
    mergeable: Sequence[str] = (),
) -> bytes:
    """Render one complete SHA-256-native FETCH_HEAD replacement."""

    if not isinstance(source, str) or not source:
        raise ValueError("FETCH_HEAD source must be non-empty")

    mergeable_set = set(mergeable)
    unknown_mergeable = mergeable_set.difference(refs)
    if unknown_mergeable:
        raise ValueError("FETCH_HEAD mergeable refs must be present in refs")

    lines: list[str] = []
    for refname, oid in refs.items():
        if not isinstance(refname, str) or not refname:
            raise ValueError("FETCH_HEAD ref names must be non-empty strings")
        if not isinstance(oid, str):
            raise TypeError("FETCH_HEAD object ids must be strings")
        canonical = oid.lower()
        if len(canonical) != 64 or any(char not in _HEX for char in canonical):
            raise ValueError("FETCH_HEAD object ids must be full 64-hex SHA-256 values")
        marker = "" if refname in mergeable_set else "not-for-merge"
        lines.append(f"{canonical}\t{marker}\t{_description(refname, source)}\n")
    return "".join(lines).encode("utf-8")


def _acquire_fetch_head_lock(pygit_dir: Path) -> tuple[int, Path]:
    """Acquire the canonical FETCH_HEAD writer lock without stealing it.

    Git's lockfile API creates ``<filename>.lock`` with ``O_CREAT|O_EXCL`` so
    concurrent writers fail rather than overwrite one another. Keep the raw
    ``FileExistsError`` contract: callers get an unambiguous contention signal
    and the existing lock remains untouched.

    The returned descriptor is explicitly non-inheritable. Python normally
    creates descriptors this way already, but making the boundary explicit
    prevents a future runtime/platform change from leaking an in-flight lock
    into a child process spawned by the fetch path.
    """

    lock_path = pygit_dir / "FETCH_HEAD.lock"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    fd = os.open(lock_path, flags, 0o666)
    try:
        os.set_inheritable(fd, False)
    except Exception:
        os.close(fd)
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass
        raise
    return fd, lock_path


def write_fetch_head_durable(
    pygit_dir: Path,
    refs: Mapping[str, str],
    *,
    source: str,
    mergeable: Sequence[str] = (),
) -> None:
    """Serialize, atomically replace, and durably publish ``FETCH_HEAD``.

    The entire replacement is rendered before lock acquisition. Publication
    then follows Git-style lockfile ownership: create ``FETCH_HEAD.lock`` with
    exclusive creation, write and ``fsync()`` it, atomically rename that exact
    lockfile to ``FETCH_HEAD``, and finally fsync the repository metadata
    directory on POSIX. Empty ``refs`` therefore durably truncates a stale file
    through the same serialized path.

    A pre-existing ``FETCH_HEAD.lock`` is never overwritten, unlinked, or
    treated as stale. If writing or replacement fails after this call acquires
    the lock, only this call's lockfile is rolled back. If the final directory
    fsync fails after replacement, the new complete FETCH_HEAD may already be
    visible; the exception propagates and callers must not advance mutable
    tracking refs.
    """

    if not isinstance(pygit_dir, Path):
        pygit_dir = Path(pygit_dir)
    data = _render_fetch_head(refs, source=source, mergeable=mergeable)

    pygit_dir.mkdir(parents=True, exist_ok=True)
    destination = pygit_dir / "FETCH_HEAD"
    fd, lock_path = _acquire_fetch_head_lock(pygit_dir)
    committed = False
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(lock_path, destination)
        committed = True
        _fsync_directory(pygit_dir)
    finally:
        if not committed:
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass
