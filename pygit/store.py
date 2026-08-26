"""
pygit/store.py
==============
Content-Addressed Object Store
================================

This is the heart of the "Git is a database" claim.

Loose objects live under ``objects/<2-hex>/<62-hex>`` and packfiles live under
``objects/pack``.  Phase 120 also supports Git-style
``objects/info/alternates`` borrowing: reads may consult transitive alternate
pygit object databases, while writes and deletes remain strictly local.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import zlib
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .objects.base import GitObject, HASH_ALGO
from .objects.blob import BlobObject
from .objects.commit import CommitObject
from .objects.tag import TagObject
from .objects.tree import TreeObject


_TYPE_MAP = {
    b"blob": BlobObject,
    b"tree": TreeObject,
    b"commit": CommitObject,
    b"tag": TagObject,
}
_LOOSE_HEX = frozenset("0123456789abcdef")


class ObjectStore:
    """Read/write interface to a primary ``.pygit/objects`` directory.

    Alternate object databases are a read-only extension of the visible object
    set.  ``write()``, ``write_raw()``, and ``delete()`` always target only
    ``self.root``; this prevents a borrowing repository from mutating stores it
    does not own.
    """

    def __init__(self, objects_dir: Path) -> None:
        self.root = Path(objects_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        # Cache MIDX files independently per primary/alternate pack directory.
        self._midx_caches: Dict[Path, Tuple[Tuple[int, int], object]] = {}

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    @staticmethod
    def _valid_loose_object(path: Path, sha: str) -> bool:
        """Return whether *path* already stores exactly the requested object."""
        try:
            store_bytes = zlib.decompress(path.read_bytes())
        except zlib.error:
            return False
        return hashlib.new(HASH_ALGO, store_bytes).hexdigest() == sha

    def write(self, obj: GitObject) -> str:
        """Serialize and atomically publish *obj* in the primary store."""
        store_bytes = obj._build_store_bytes()
        sha = hashlib.new(HASH_ALGO, store_bytes).hexdigest()
        obj_path = self._path_for(sha)

        if obj_path.exists() and self._valid_loose_object(obj_path, sha):
            return sha

        obj_path.parent.mkdir(parents=True, exist_ok=True)
        compressed = zlib.compress(store_bytes)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".tmp-{sha}-",
            dir=str(obj_path.parent),
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(compressed)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, obj_path)
        finally:
            if temp_path.exists():
                temp_path.unlink()

        return sha

    def write_raw(self, data: bytes) -> str:
        """Hash and store arbitrary raw bytes as a local blob."""
        return self.write(BlobObject(data))

    # ------------------------------------------------------------------
    # Visible storage roots
    # ------------------------------------------------------------------

    def storage_roots(self) -> Tuple[Path, ...]:
        """Return primary plus transitive alternate object directories.

        The primary root is always first.  Alternate discovery is intentionally
        evaluated on each call so edits to ``objects/info/alternates`` become
        visible without reconstructing the Repository/ObjectStore instance.
        """
        from .alternates import alternate_object_dirs

        primary = self.root.resolve()
        return (primary,) + alternate_object_dirs(primary)

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def read(self, sha: str) -> GitObject:
        """Read *sha* from the primary store or its alternate databases.

        Corruption in the primary object database remains a hard error rather
        than being hidden by an alternate.  If an alternate copy is damaged,
        later alternates may still provide an independent valid copy; the first
        alternate storage error is surfaced only when no valid copy exists.
        """
        primary = self.root.resolve()
        try:
            return self._read_local(primary, sha)
        except KeyError:
            pass

        first_error = None
        for root in self.storage_roots()[1:]:
            try:
                return self._read_local(root, sha)
            except KeyError:
                continue
            except (ValueError, OSError, zlib.error) as exc:
                if first_error is None:
                    first_error = exc

        if first_error is not None:
            raise first_error
        raise KeyError(f"Object not found: {sha}")

    def _read_local(self, root: Path, sha: str) -> GitObject:
        obj_path = self._path_for_root(root, sha)
        if obj_path.exists():
            compressed = obj_path.read_bytes()
            store_bytes = zlib.decompress(compressed)
            actual_sha = hashlib.new(HASH_ALGO, store_bytes).hexdigest()
            if actual_sha != sha:
                raise ValueError(
                    f"Object {sha} is corrupt (stored hash is {actual_sha})"
                )
            return self._parse(store_bytes)

        return self._read_packed_local(root, sha)

    def _read_packed_local(self, root: Path, sha: str) -> GitObject:
        """Read one OID from packs under exactly one object database."""
        from .pack import PackReader

        pack_dir = root / "pack"
        if not pack_dir.exists():
            raise KeyError(sha)

        midx = self._load_multi_pack_index(pack_dir)
        first_error = None
        selected_idx = None
        covered = set()

        if midx is not None:
            covered.update(midx.pack_names)
            mapping = midx.lookup(sha)
            if mapping is not None:
                selected_idx = pack_dir / mapping.pack_name
                if not selected_idx.is_file():
                    first_error = FileNotFoundError(selected_idx)
                elif not selected_idx.with_suffix(".pack").is_file():
                    first_error = FileNotFoundError(selected_idx.with_suffix(".pack"))
                else:
                    try:
                        reader = PackReader(selected_idx)
                        if not reader.has_object(sha):
                            first_error = ValueError(
                                f"multi-pack-index mapping for {sha} is absent from {mapping.pack_name}"
                            )
                        else:
                            obj = reader.read_object(sha)
                            if obj is not None:
                                return obj
                            first_error = ValueError(
                                f"multi-pack-index could not read {sha} from {mapping.pack_name}"
                            )
                    except (ValueError, OSError, zlib.error) as exc:
                        first_error = exc

        # Healthy MIDX misses can skip covered indexes.  If the selected copy
        # failed, inspect every other pack so duplicate storage remains useful.
        for idx_file in pack_dir.glob("*.idx"):
            if selected_idx is not None and idx_file == selected_idx:
                continue
            if selected_idx is None and idx_file.name in covered:
                continue
            try:
                reader = PackReader(idx_file)
                if not reader.has_object(sha):
                    continue
                obj = reader.read_object(sha)
                if obj is not None:
                    return obj
            except (ValueError, OSError, zlib.error) as exc:
                if first_error is None:
                    first_error = exc

        if first_error is not None:
            raise first_error
        raise KeyError(sha)

    def exists(self, sha: str) -> bool:
        """Return True when *sha* exists locally or in an alternate store."""
        for root in self.storage_roots():
            if self._exists_local(root, sha):
                return True
        return False

    def _exists_local(self, root: Path, sha: str) -> bool:
        if self._path_for_root(root, sha).exists():
            return True

        from .pack import PackReader

        pack_dir = root / "pack"
        if not pack_dir.exists():
            return False

        midx = self._load_multi_pack_index(pack_dir)
        covered = set()
        if midx is not None:
            covered.update(midx.pack_names)
            mapping = midx.lookup(sha)
            if mapping is not None:
                idx_file = pack_dir / mapping.pack_name
                if not idx_file.is_file() or not idx_file.with_suffix(".pack").is_file():
                    raise ValueError(
                        f"multi-pack-index references missing pack {mapping.pack_name}"
                    )
                if not PackReader(idx_file).has_object(sha):
                    raise ValueError(
                        f"multi-pack-index mapping for {sha} is absent from {mapping.pack_name}"
                    )
                return True

        for idx_file in sorted(pack_dir.glob("*.idx")):
            if idx_file.name in covered:
                continue
            if PackReader(idx_file).has_object(sha):
                return True
        return False

    def delete(self, sha: str) -> bool:
        """Delete only a loose object owned by the primary store."""
        path = self._path_for(sha)
        if path.exists():
            path.unlink()
            if path.parent.is_dir() and not any(path.parent.iterdir()):
                path.parent.rmdir()
            return True
        return False

    def all_shas(self) -> List[str]:
        """Return all accessible canonical object IDs, including alternates."""
        shas = set()
        for root in self.storage_roots():
            shas.update(self._local_shas(root))
        return sorted(shas)

    def _local_shas(self, root: Path) -> set:
        shas = set()
        if root.exists():
            for prefix_dir in sorted(root.iterdir()):
                prefix = prefix_dir.name
                if (
                    not prefix_dir.is_dir()
                    or len(prefix) != 2
                    or any(ch not in _LOOSE_HEX for ch in prefix)
                ):
                    continue
                for obj_file in sorted(prefix_dir.iterdir()):
                    suffix = obj_file.name
                    if (
                        obj_file.is_file()
                        and len(suffix) == 62
                        and all(ch in _LOOSE_HEX for ch in suffix)
                    ):
                        shas.add(prefix + suffix)

        from .pack import PackReader

        pack_dir = root / "pack"
        if pack_dir.exists():
            midx = self._load_multi_pack_index(pack_dir)
            covered = set()
            if midx is not None:
                covered.update(midx.pack_names)
                shas.update(entry.oid for entry in midx.entries)
            for idx_file in sorted(pack_dir.glob("*.idx")):
                if idx_file.name in covered:
                    continue
                shas.update(PackReader(idx_file).get_shas())
        return shas

    def resolve_prefix(self, prefix: str) -> Optional[str]:
        """Resolve a unique 4+ hex prefix across primary and alternates."""
        prefix = prefix.lower()
        if len(prefix) == 64:
            return prefix if self.exists(prefix) else None
        if len(prefix) < 4:
            return None

        matches = [sha for sha in self.all_shas() if sha.startswith(prefix)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError(
                f"Ambiguous short SHA prefix '{prefix}': matches {len(matches)} objects"
            )
        return None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _load_multi_pack_index(self, pack_dir: Path):
        pack_dir = Path(pack_dir).resolve()
        midx_path = pack_dir / "multi-pack-index"
        if not midx_path.is_file():
            self._midx_caches.pop(pack_dir, None)
            return None

        stat = midx_path.stat()
        key = (stat.st_mtime_ns, stat.st_size)
        cached = self._midx_caches.get(pack_dir)
        if cached is None or cached[0] != key:
            from .multi_pack_index import parse_multi_pack_index

            parsed = parse_multi_pack_index(midx_path)
            self._midx_caches[pack_dir] = (key, parsed)
            return parsed
        return cached[1]

    def _path_for(self, sha: str) -> Path:
        return self._path_for_root(self.root, sha)

    @staticmethod
    def _path_for_root(root: Path, sha: str) -> Path:
        return Path(root) / sha[:2] / sha[2:]

    @staticmethod
    def _parse(store_bytes: bytes) -> GitObject:
        null_pos = store_bytes.index(b"\x00")
        header = store_bytes[:null_pos]
        payload = store_bytes[null_pos + 1 :]

        parts = header.split(b" ", 1)
        type_name = parts[0]
        declared_size = int(parts[1])
        if len(payload) != declared_size:
            raise ValueError(
                f"Size mismatch: header says {declared_size}, got {len(payload)}"
            )

        klass = _TYPE_MAP.get(type_name)
        if klass is None:
            raise ValueError(f"Unknown object type: {type_name!r}")

        obj = klass.__new__(klass)
        obj.deserialize(payload)
        return obj
