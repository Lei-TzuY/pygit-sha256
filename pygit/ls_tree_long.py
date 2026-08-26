"""Long-form formatter for ``pygit ls-tree -l/--long``.

Git's long mode adds an object-size column to the ordinary ls-tree record.
Blob entries report their serialized payload size; tree and gitlink entries
report ``-``.  This module is presentation-only and keeps traversal in the
Phase 76 ``pygit.ls_tree`` implementation.
"""

from __future__ import annotations

from typing import Iterable, Optional

from .ls_tree import LsTreeEntry
from .objects import BlobObject
from .repo import Repository
from .revision import abbreviate_oid


def _object_name(repo: Repository, entry: LsTreeEntry, abbrev: Optional[int]) -> str:
    if abbrev is None:
        return entry.oid
    if abbrev < 4 or abbrev > 64:
        raise ValueError("ls-tree abbreviation length must be between 4 and 64")
    if repo.store.exists(entry.oid):
        return abbreviate_oid(repo, entry.oid, minimum=abbrev)
    return entry.oid[:abbrev]


def _object_size(repo: Repository, entry: LsTreeEntry) -> str:
    """Return Git's long-mode size field before padding."""

    if entry.object_type != "blob":
        return "-"
    obj = repo.store.read(entry.oid)
    if not isinstance(obj, BlobObject):
        raise RuntimeError(
            f"blob tree entry {entry.path!r} references non-blob object {entry.oid}"
        )
    return str(len(obj.serialize()))


def format_ls_tree_long(
    repo: Repository,
    entries: Iterable[LsTreeEntry],
    *,
    abbrev: Optional[int] = None,
    nul_terminated: bool = False,
) -> bytes:
    """Format entries using Git's ``ls-tree -l`` record shape.

    The size field is right-aligned to seven characters, matching
    ``%(objectsize:padded)`` in native Git.  Tree and gitlink records use ``-``.
    """

    lines = []
    for entry in entries:
        object_name = _object_name(repo, entry, abbrev)
        size = _object_size(repo, entry)
        lines.append(
            f"{entry.mode} {entry.object_type} {object_name} {size:>7}\t{entry.path}"
        )

    if not lines:
        return b""
    separator = "\x00" if nul_terminated else "\n"
    return (separator.join(lines) + separator).encode("utf-8")
