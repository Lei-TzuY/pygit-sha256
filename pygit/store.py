"""
pygit/store.py
==============
Content-Addressed Object Store
================================

This is the heart of the "Git is a database" claim.

**How it works:**

1. Take any :class:`~pygit.objects.base.GitObject`.
2. Serialize it to bytes (header + payload).
3. Hash those bytes with SHA-256.
4. Store the bytes at ``.pygit/objects/<first-2-chars>/<remaining-62-chars>``.

Because the path IS the hash of the content, the same content always
lands at the same path — two identical files share one blob.  Corruption
is detectable: re-hash the file and compare.

The two-level directory split (2-char prefix / 62-char suffix) limits the
number of files per directory on filesystems with poor large-directory
performance.  Real Git uses 2/38 (40-hex SHA-1); we use 2/62 (64-hex SHA-256).

All objects are stored compressed with **zlib** (deflate), just like
real Git.
"""

from __future__ import annotations
import hashlib
import zlib
import os
from pathlib import Path
from typing import Union

from .objects.base   import GitObject, HASH_ALGO
from .objects.blob   import BlobObject
from .objects.tree   import TreeObject
from .objects.commit import CommitObject


# Map type-name bytes → concrete class for deserialisation
_TYPE_MAP = {
    b"blob":   BlobObject,
    b"tree":   TreeObject,
    b"commit": CommitObject,
}


class ObjectStore:
    """
    Read/write interface to the ``.pygit/objects`` directory.

    Parameters
    ----------
    objects_dir : Path
        Absolute path to the ``.pygit/objects`` directory.
    """

    def __init__(self, objects_dir: Path) -> None:
        self.root = objects_dir
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def write(self, obj: GitObject) -> str:
        """
        Serialise *obj*, compress with zlib, and write to disk.

        Returns the hex-digest (the object's "name" in the store).

        If the object already exists on disk this is a no-op (content-
        addressed storage is idempotent by definition).
        """
        store_bytes = obj._build_store_bytes()
        sha = hashlib.new(HASH_ALGO, store_bytes).hexdigest()

        obj_path = self._path_for(sha)
        if not obj_path.exists():
            obj_path.parent.mkdir(parents=True, exist_ok=True)
            compressed = zlib.compress(store_bytes)
            obj_path.write_bytes(compressed)

        return sha

    def write_raw(self, data: bytes) -> str:
        """
        Hash-and-store arbitrary raw bytes as a blob.

        Convenience wrapper used by ``hash-object``.  Returns the SHA.
        """
        blob = BlobObject(data)
        return self.write(blob)

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def read(self, sha: str) -> GitObject:
        """
        Read the object identified by *sha* from disk.

        Raises
        ------
        KeyError
            If the object is not found in the store.
        ValueError
            If the stored data is corrupt (hash mismatch or bad format).
        """
        obj_path = self._path_for(sha)
        if not obj_path.exists():
            raise KeyError(f"Object not found: {sha}")

        compressed = obj_path.read_bytes()
        store_bytes = zlib.decompress(compressed)

        # Verify integrity: re-hash and compare
        actual_sha = hashlib.new(HASH_ALGO, store_bytes).hexdigest()
        if actual_sha != sha:
            raise ValueError(
                f"Object {sha} is corrupt "
                f"(stored hash is {actual_sha})"
            )

        return self._parse(store_bytes)

    def exists(self, sha: str) -> bool:
        """Return True if the object exists in the store."""
        return self._path_for(sha).exists()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _path_for(self, sha: str) -> Path:
        """Map a hex SHA to its on-disk path."""
        return self.root / sha[:2] / sha[2:]

    @staticmethod
    def _parse(store_bytes: bytes) -> GitObject:
        """
        Decode the object envelope ``<type> <size>\\x00<payload>``
        and return a populated concrete GitObject.
        """
        null_pos = store_bytes.index(b"\x00")
        header   = store_bytes[:null_pos]
        payload  = store_bytes[null_pos + 1:]

        parts   = header.split(b" ", 1)
        type_name = parts[0]
        declared_size = int(parts[1])

        if len(payload) != declared_size:
            raise ValueError(
                f"Size mismatch: header says {declared_size}, "
                f"got {len(payload)}"
            )

        klass = _TYPE_MAP.get(type_name)
        if klass is None:
            raise ValueError(f"Unknown object type: {type_name!r}")

        obj = klass.__new__(klass)
        obj.deserialize(payload)
        return obj
