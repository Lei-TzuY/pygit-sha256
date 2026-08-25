"""Typed ``hash-object`` plumbing for files, stdin, and object-store writes.

The legacy command only hashed one file as a blob.  These helpers expose the
useful low-level core directly: exact SHA-256 object envelopes for the four
native object types, optional validation, and idempotent loose-object writes.
"""

from __future__ import annotations

import hashlib
import zlib
from pathlib import Path
from typing import Optional

from .objects import BlobObject, CommitObject, TagObject, TreeObject
from .objects.base import HASH_ALGO, GitObject
from .repo import Repository


_OBJECT_CLASSES = {
    "blob": BlobObject,
    "tree": TreeObject,
    "commit": CommitObject,
    "tag": TagObject,
}
_HEX = frozenset("0123456789abcdef")


def _is_oid(value: str) -> bool:
    return len(value) == 64 and all(char in _HEX for char in value.lower())


def object_envelope(data: bytes, object_type: str = "blob") -> bytes:
    """Return the exact ``<type> <size>\0<payload>`` bytes used for hashing."""
    if object_type not in _OBJECT_CLASSES:
        raise ValueError(f"unsupported object type: {object_type!r}")
    return f"{object_type} {len(data)}\0".encode("ascii") + data


def _parse_payload(data: bytes, object_type: str) -> GitObject:
    """Parse a payload and reject structurally incomplete native objects."""
    klass = _OBJECT_CLASSES.get(object_type)
    if klass is None:
        raise ValueError(f"unsupported object type: {object_type!r}")

    if object_type == "blob":
        return BlobObject(data)

    obj = klass.__new__(klass)
    try:
        obj.deserialize(data)
    except (AttributeError, UnicodeDecodeError, ValueError, IndexError) as exc:
        raise ValueError(f"invalid {object_type} payload: {exc}") from exc

    if isinstance(obj, CommitObject):
        if not getattr(obj, "tree", None) or not _is_oid(obj.tree):
            raise ValueError("invalid commit payload: missing or invalid tree object ID")
        if any(not _is_oid(parent) for parent in getattr(obj, "parents", [])):
            raise ValueError("invalid commit payload: invalid parent object ID")
        if not hasattr(obj, "author") or not hasattr(obj, "committer"):
            raise ValueError("invalid commit payload: missing author or committer")
    elif isinstance(obj, TagObject):
        target = getattr(obj, "target_sha", "")
        target_type = getattr(obj, "target_type", b"")
        if not _is_oid(target):
            raise ValueError("invalid tag payload: missing or invalid target object ID")
        if target_type not in {b"blob", b"tree", b"commit", b"tag"}:
            raise ValueError("invalid tag payload: unsupported target type")
        if not getattr(obj, "tag_name", "") or not hasattr(obj, "tagger"):
            raise ValueError("invalid tag payload: missing tag name or tagger")
    elif isinstance(obj, TreeObject):
        for entry in obj.entries:
            if not _is_oid(entry.sha):
                raise ValueError("invalid tree payload: invalid entry object ID")

    return obj


def hash_object_data(data: bytes, object_type: str = "blob", *, validate: bool = True) -> str:
    """Hash one native object payload without writing it to a repository."""
    if validate:
        _parse_payload(data, object_type)
    envelope = object_envelope(data, object_type)
    return hashlib.new(HASH_ALGO, envelope).hexdigest()


def write_object_data(repo: Repository, data: bytes, object_type: str = "blob") -> str:
    """Validate and write one exact native object payload as a loose object."""
    _parse_payload(data, object_type)
    envelope = object_envelope(data, object_type)
    oid = hashlib.new(HASH_ALGO, envelope).hexdigest()
    path = repo.store.root / oid[:2] / oid[2:]
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(zlib.compress(envelope))
    return oid


def hash_path(path: str | Path, object_type: str = "blob", *, repo: Optional[Repository] = None, write: bool = False) -> str:
    """Hash one filesystem path, optionally storing the resulting object."""
    data = Path(path).read_bytes()
    if write:
        if repo is None:
            raise ValueError("write=True requires a repository")
        return write_object_data(repo, data, object_type)
    return hash_object_data(data, object_type)
