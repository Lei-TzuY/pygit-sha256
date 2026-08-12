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
from .objects.tag    import TagObject


# Map type-name bytes → concrete class for deserialisation
_TYPE_MAP = {
    b"blob":   BlobObject,
    b"tree":   TreeObject,
    b"commit": CommitObject,
    b"tag":    TagObject,
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
        Read the object identified by *sha* from disk (loose or pack).

        Raises
        ------
        KeyError
            If the object is not found in the store.
        ValueError
            If the stored data is corrupt (hash mismatch or bad format).
        """
        obj_path = self._path_for(sha)
        if obj_path.exists():
            compressed = obj_path.read_bytes()
            store_bytes = zlib.decompress(compressed)
            actual_sha = hashlib.new(HASH_ALGO, store_bytes).hexdigest()
            if actual_sha != sha:
                raise ValueError(f"Object {sha} is corrupt (stored hash is {actual_sha})")
            return self._parse(store_bytes)

        # Check packfiles
        from .pack import PackReader
        pack_dir = self.root / "pack"
        if pack_dir.exists():
            for idx_file in pack_dir.glob("*.idx"):
                reader = PackReader(idx_file)
                if reader.has_object(sha):
                    obj = reader.read_object(sha)
                    if obj:
                        return obj

        raise KeyError(f"Object not found: {sha}")

    def exists(self, sha: str) -> bool:
        """Return True if the object exists in loose or pack storage."""
        if self._path_for(sha).exists():
            return True
        from .pack import PackReader
        pack_dir = self.root / "pack"
        if pack_dir.exists():
            for idx_file in pack_dir.glob("*.idx"):
                if PackReader(idx_file).has_object(sha):
                    return True
        return False

    def delete(self, sha: str) -> bool:
        """Delete a loose object from disk. Returns True if deleted."""
        p = self._path_for(sha)
        if p.exists():
            p.unlink()
            if p.parent.is_dir() and not any(p.parent.iterdir()):
                p.parent.rmdir()
            return True
        return False

    def all_shas(self) -> List[str]:
        """Return a list of all 64-char object SHAs stored on disk (loose and pack)."""
        shas: set = set()
        if self.root.exists():
            for prefix_dir in sorted(self.root.iterdir()):
                if prefix_dir.is_dir() and len(prefix_dir.name) == 2:
                    for obj_file in sorted(prefix_dir.iterdir()):
                        if obj_file.is_file():
                            shas.add(prefix_dir.name + obj_file.name)
        from .pack import PackReader
        pack_dir = self.root / "pack"
        if pack_dir.exists():
            for idx_file in pack_dir.glob("*.idx"):
                shas.update(PackReader(idx_file).get_shas())
        return sorted(shas)

    def resolve_prefix(self, prefix: str) -> Optional[str]:
        """
        Resolve a short SHA prefix (4+ hex chars) to a full 64-char SHA.

        Returns full SHA if unique match found, raises ValueError if ambiguous,
        returns None if no object matches.
        """
        prefix = prefix.lower()
        if len(prefix) == 64:
            return prefix if self.exists(prefix) else None
        if len(prefix) < 4:
            return None

        dir_prefix = prefix[:2]
        file_prefix = prefix[2:]
        target_dir = self.root / dir_prefix
        if not target_dir.is_dir():
            return None

        matches = [
            dir_prefix + f.name
            for f in target_dir.iterdir()
            if f.is_file() and f.name.startswith(file_prefix)
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError(f"Ambiguous short SHA prefix '{prefix}': matches {len(matches)} objects")
        return None

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
