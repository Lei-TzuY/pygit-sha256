"""Native export support for Phase204 stable shallow commits.

Phase204 stores original Git parent identities in ``parent-sha1`` headers so a
commit imported from a genuinely truncated pack keeps one stable local SHA-256
identity while the repository is deepened.  The historical ``NativeExporter``
only serializes ``CommitObject.parents``; at a shallow boundary that runtime
list is intentionally empty, which would silently change the exported native
commit and its SHA-1.

This module installs one narrow compatibility extension on the existing
``NativeExporter`` class. Ordinary pygit commits still use the historical
implementation unchanged. Foreign commits instead serialize their preserved
native parent list verbatim while exporting any fully resolved local parents for
the outgoing pack when appropriate.
"""

from __future__ import annotations

import hashlib
from typing import Callable

from .objects import CommitObject
from .remote import NativeExporter, NativeObject


_INSTALLED = False


def _validate_native_parent(oid: str) -> str:
    value = str(oid).lower()
    if len(value) != 40:
        raise ValueError("foreign commit parent must be a 40-hex SHA-1 object id")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError(
            "foreign commit parent must be a 40-hex SHA-1 object id"
        ) from exc
    return value


def _export_foreign_commit(exporter: NativeExporter, sha: str, obj: CommitObject) -> str:
    """Export one stable foreign commit without requiring omitted parents.

    If all direct parents are currently resolved locally, recursively exporting
    them preserves normal push behavior to a different remote and verifies that
    their reconstructed native identities still match the preserved parent
    headers. At a shallow boundary ``obj.parents`` is empty by design, so the
    native parent references remain external and no nonexistent local object is
    invented.
    """

    native_parents = [
        _validate_native_parent(parent)
        for parent in (obj.native_parents or [])
    ]

    if obj.parents:
        if len(obj.parents) != len(native_parents):
            raise RuntimeError(
                "foreign commit has inconsistent resolved/native parent counts"
            )
        for local_parent, expected_native in zip(obj.parents, native_parents):
            exported_parent = exporter.export_oid(local_parent)
            if exported_parent != expected_native:
                raise RuntimeError(
                    "resolved foreign parent does not round-trip to its preserved "
                    f"native object id: expected {expected_native}, got {exported_parent}"
                )

    lines = [f"tree {exporter.export_oid(obj.tree)}"]
    lines.extend(f"parent {parent}" for parent in native_parents)
    lines.append(f"author {obj.author.encode()}")
    lines.append(f"committer {obj.committer.encode()}")
    lines.extend(("", obj.message))
    data = "\n".join(lines).encode()
    canonical = f"commit {len(data)}\0".encode() + data
    native_oid = hashlib.sha1(canonical).hexdigest()
    exporter.converted[sha] = native_oid
    exporter.objects[native_oid] = NativeObject("commit", data, native_oid)
    return native_oid


def install_native_export_shallow_support() -> None:
    """Teach the existing ``NativeExporter`` about Phase204 foreign commits."""

    global _INSTALLED
    if _INSTALLED:
        return

    original: Callable[[NativeExporter, str], str] = NativeExporter.export_oid

    def export_oid(self: NativeExporter, sha: str) -> str:
        if sha in self.converted:
            return self.converted[sha]
        known = self.known_oids.get(sha)
        if known and sha in self.have_shas:
            self.converted[sha] = known
            return known

        obj = self.store.read(sha)
        if isinstance(obj, CommitObject) and obj.native_parents is not None:
            return _export_foreign_commit(self, sha, obj)
        return original(self, sha)

    NativeExporter.export_oid = export_oid
    _INSTALLED = True
