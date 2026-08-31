"""Crash-safe FETCH_HEAD replacement for SHA-256-native fetches.

Phase344 adds a durability boundary for the replace-style FETCH_HEAD writes used
by the mapped incremental packfile-URI path.  The existing formatter remains
authoritative; this module only strengthens publication ordering.
"""

from __future__ import annotations

import os
import tempfile
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


def write_fetch_head_durable(
    pygit_dir: Path,
    refs: Mapping[str, str],
    *,
    source: str,
    mergeable: Sequence[str] = (),
) -> None:
    """Atomically replace FETCH_HEAD and fence the directory before success.

    The replacement is written to a same-directory temporary file, flushed with
    ``fsync()``, atomically installed with ``os.replace()``, and followed by a
    directory ``fsync`` on POSIX.  Empty ``refs`` therefore durably truncates a
    stale FETCH_HEAD through the same crash-safe sequence.

    If the final directory fsync fails, the new FETCH_HEAD may already be
    visible.  The exception is propagated and callers must not advance mutable
    tracking refs.  Retrying is safe because the whole file is replaced from a
    deterministic rendering.
    """

    if not isinstance(pygit_dir, Path):
        pygit_dir = Path(pygit_dir)
    data = _render_fetch_head(refs, source=source, mergeable=mergeable)

    pygit_dir.mkdir(parents=True, exist_ok=True)
    destination = pygit_dir / "FETCH_HEAD"
    fd, tmp_name = tempfile.mkstemp(prefix="FETCH_HEAD.", suffix=".lock", dir=pygit_dir)
    tmp_path = Path(tmp_name)
    installed = False
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, destination)
        installed = True
        _fsync_directory(pygit_dir)
    finally:
        if not installed:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass
