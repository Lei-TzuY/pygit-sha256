"""Durability boundary for Git-compatible loose-object maps.

Phase341 makes LMAP publication conflict-free and atomically visible to readers.
Phase342 adds an explicit success-after-durability API: the immutable map is
published first, then the object-map directory and its parent objects directory
are fsynced before success is returned.

This wrapper deliberately preserves Phase341's SHA-1/SHA-256 validation and
repository-wide conflict lock rather than duplicating the LMAP writer.
"""

from __future__ import annotations

import os
from pathlib import Path

from .loose_object_map import PublishedLooseObjectMap, publish_staged_loose_object_map
from .protocol_v2_packfile_uri_stage import StagedPackfileUriImport
from .repo import Repository


def _fsync_directory(path: Path) -> None:
    """Flush one directory entry namespace when the platform supports it.

    POSIX filesystems require a directory fsync to make link/create/unlink
    namespace changes durable across a power loss. Windows does not expose the
    same directory-fd contract through ``os.open``; the underlying contents API
    remains atomic there, while this explicit durability fence is a POSIX
    guarantee.
    """

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


def publish_staged_loose_object_map_durable(
    repo: Repository,
    staged: StagedPackfileUriImport,
) -> PublishedLooseObjectMap:
    """Publish one validated LMAP and durably fence its namespace before return.

    Phase341 remains authoritative for content authentication, LMAP encoding,
    cross-generation conflict rejection, exclusive writer locking, temporary
    file fsync, and atomic hard-link publication. After that atomic publication
    has completed, this boundary fsyncs ``objects/object-map`` and then
    ``objects`` so both the map filename and a newly-created object-map
    directory are durable before the caller may treat publication as successful.

    If either durability fence fails, the exception is propagated. The immutable
    map may already be visible, which is safe and intentionally idempotent:
    callers can retry and Phase341 will validate/reuse the exact content-addressed
    generation instead of manufacturing a new identity.
    """

    published = publish_staged_loose_object_map(repo, staged)
    directory = published.path.parent
    _fsync_directory(directory)
    _fsync_directory(directory.parent)
    return published
