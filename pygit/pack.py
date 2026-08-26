"""
pygit/pack.py
==============
Packfile & Fan-out Index (.pack / .idx) Engine
==============================================

Implements native Git packfile creation and reading.

Packfile (.pack) Binary Structure:
----------------------------------
1. Header (12 bytes):
     b"PACK" (4 bytes)
     Version = 2 (4 bytes, big-endian)
     Object Count = N (4 bytes, big-endian)
2. N Concatenated Compressed Objects:
     Varint header encoding (object_type + size)
     zlib.compress(store_bytes)
3. Checksum (32 bytes):
     SHA-256 digest of header + object stream.

Index File (.idx) Binary Structure:
-----------------------------------
1. Header (8 bytes):
     b"\xfftOc" (4 bytes)
     Version = 2 (4 bytes, big-endian)
2. Fan-out Table (256 * 4 = 1024 bytes):
     Cumulative counts of objects whose SHA-256 starts with byte values 0x00..0xff.
3. Sorted SHA-256 Table (N * 64 hex chars or 32 raw bytes):
     Sorted list of 64-char SHA-256 hex strings.
4. CRC-32 Table (N * 4 bytes).
5. Offset Table (N * 4 bytes):
     Byte offsets into the .pack file for each object.
6. Checksum (32 bytes):
     SHA-256 digest of the index file payload.
"""

from __future__ import annotations

import hashlib
import os
import struct
import tempfile
import zlib
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .objects import BlobObject, CommitObject, GitObject, TreeObject, TagObject
from .objects.base import HASH_ALGO

_TYPE_ID_MAP = {
    b"commit": 1,
    b"tree":   2,
    b"blob":   3,
    b"tag":    4,
}

_ID_TYPE_MAP = {v: k for k, v in _TYPE_ID_MAP.items()}


class PackWriter:
    """Creates a paired .pack and .idx file from a collection of GitObjects."""

    def __init__(self, objects: List[Tuple[str, GitObject]]) -> None:
        # Sort objects by SHA-256 hex string
        self.objects = sorted(objects, key=lambda x: x[0])

    @staticmethod
    def _stage_bytes(final_path: Path, data: bytes) -> Path:
        """Write and fsync *data* to a hidden same-directory temporary file."""
        handle = tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".tmp-{final_path.name}-",
            dir=str(final_path.parent),
            delete=False,
        )
        temp_path = Path(handle.name)
        try:
            with handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
            raise
        return temp_path

    @staticmethod
    def _matches_expected(path: Path, data: bytes) -> bool:
        return path.is_file() and path.read_bytes() == data

    @classmethod
    def _publish_pair(
        cls,
        pack_path: Path,
        pack_data: bytes,
        idx_path: Path,
        idx_data: bytes,
    ) -> None:
        """Publish an immutable pack/index pair with the index as commit point.

        Pack names are content-derived, so an existing final path must contain
        exactly the bytes this writer would produce. A matching complete pair is
        an idempotent no-op; a matching one-file orphan is completed in place.

        For a new pair, both files are fully staged and fsynced before either
        final path appears. The pack is installed first and the index last, so a
        crash cannot make an index advertise a pack that has not been published.
        If the second rename fails synchronously, the newly published pack is
        rolled back and all remaining temporary files are removed.
        """
        pack_exists = pack_path.exists()
        idx_exists = idx_path.exists()

        if pack_exists and not cls._matches_expected(pack_path, pack_data):
            raise RuntimeError(f"pack target collision: {pack_path.name}")
        if idx_exists and not cls._matches_expected(idx_path, idx_data):
            raise RuntimeError(f"pack index target collision: {idx_path.name}")
        if pack_exists and idx_exists:
            return

        pack_temp: Optional[Path] = None
        idx_temp: Optional[Path] = None
        published_new_pack = False
        try:
            if not pack_exists:
                pack_temp = cls._stage_bytes(pack_path, pack_data)
            if not idx_exists:
                idx_temp = cls._stage_bytes(idx_path, idx_data)

            if pack_temp is not None:
                os.replace(pack_temp, pack_path)
                pack_temp = None
                published_new_pack = True

            if idx_temp is not None:
                try:
                    os.replace(idx_temp, idx_path)
                    idx_temp = None
                except Exception:
                    if published_new_pack:
                        try:
                            pack_path.unlink()
                        except OSError:
                            pass
                    raise
        finally:
            for temp_path in (pack_temp, idx_temp):
                if temp_path is not None:
                    try:
                        temp_path.unlink()
                    except FileNotFoundError:
                        pass

    def write_pack_and_idx(self, output_dir: Path, name_prefix: str = "pack") -> Tuple[Path, Path]:
        output_dir.mkdir(parents=True, exist_ok=True)

        # Build .pack binary stream
        pack_data = bytearray()
        pack_data.extend(b"PACK")
        pack_data.extend(struct.pack(">II", 2, len(self.objects)))

        offsets: List[int] = []
        crcs: List[int] = []
        shas: List[str] = []

        for sha, obj in self.objects:
            shas.append(sha)
            offset = len(pack_data)
            offsets.append(offset)

            # Object payload
            store_bytes = obj._build_store_bytes()
            type_id = _TYPE_ID_MAP.get(obj.type_name, 3)

            # Varint header encoding (type + size)
            size = len(store_bytes)
            first_byte = ((type_id & 0x07) << 4) | (size & 0x0F)
            size >>= 4
            header_bytes = bytearray()
            if size > 0:
                first_byte |= 0x80
            header_bytes.append(first_byte)
            while size > 0:
                b = size & 0x7F
                size >>= 7
                if size > 0:
                    b |= 0x80
                header_bytes.append(b)

            compressed = zlib.compress(store_bytes)
            entry_bytes = bytes(header_bytes) + compressed

            # CRC32
            crc = zlib.crc32(entry_bytes) & 0xFFFFFFFF
            crcs.append(crc)

            pack_data.extend(entry_bytes)

        # Pack SHA-256 checksum
        pack_checksum = hashlib.sha256(pack_data).digest()
        pack_data.extend(pack_checksum)

        # Hash packfile content for filename
        pack_name = hashlib.sha256(pack_checksum).hexdigest()[:40]
        pack_path = output_dir / f"{name_prefix}-{pack_name}.pack"
        idx_path = output_dir / f"{name_prefix}-{pack_name}.idx"

        # Build .idx binary stream
        idx_data = bytearray()
        idx_data.extend(b"\xfftOc")
        idx_data.extend(struct.pack(">I", 2))

        # 256-entry fan-out table
        counts = [0] * 256
        for sha in shas:
            first_byte = int(sha[:2], 16)
            counts[first_byte] += 1

        cum_count = 0
        for i in range(256):
            cum_count += counts[i]
            idx_data.extend(struct.pack(">I", cum_count))

        # Sorted SHA-256 table (stored as 64-char UTF-8 or 32 raw bytes)
        for sha in shas:
            idx_data.extend(sha.encode("utf-8"))

        # CRC-32 table
        for crc in crcs:
            idx_data.extend(struct.pack(">I", crc))

        # Offset table
        for off in offsets:
            idx_data.extend(struct.pack(">I", off))

        # Idx SHA-256 checksum
        idx_checksum = hashlib.sha256(idx_data).digest()
        idx_data.extend(idx_checksum)

        self._publish_pair(pack_path, bytes(pack_data), idx_path, bytes(idx_data))
        return pack_path, idx_path


class PackReader:
    """Reads validated objects from a paired .pack and .idx file."""

    def __init__(self, idx_path: Path) -> None:
        self.idx_path = idx_path
        self.pack_path = idx_path.with_suffix(".pack")
        self._shas: List[str] = []
        self._offsets: Dict[str, int] = {}
        self._crcs: Dict[str, int] = {}
        self._pack_bytes: Optional[bytes] = None
        self._payload_end: Optional[int] = None
        self._load_idx()

    def _load_idx(self) -> None:
        if not self.idx_path.exists() or not self.pack_path.exists():
            return

        from .pack_index import parse_index

        index = parse_index(self.idx_path)
        self._shas = [entry.oid for entry in index.entries]
        self._offsets = {entry.oid: entry.offset for entry in index.entries}
        self._crcs = {entry.oid: entry.crc32 for entry in index.entries}

    def _load_pack_image(self) -> Tuple[bytes, int]:
        """Load and validate the pack envelope without decompressing objects."""
        if self._pack_bytes is not None and self._payload_end is not None:
            return self._pack_bytes, self._payload_end

        if not self.pack_path.is_file():
            raise FileNotFoundError(self.pack_path)
        pack_bytes = self.pack_path.read_bytes()
        if len(pack_bytes) < 44:
            raise ValueError("pack file is too short")
        if pack_bytes[:4] != b"PACK":
            raise ValueError("invalid pack signature")

        version, object_count = struct.unpack(">II", pack_bytes[4:12])
        if version != 2:
            raise ValueError(f"unsupported pack version: {version}")
        if object_count != len(self._shas):
            raise ValueError(
                f"pack/index object count mismatch: pack has {object_count}, "
                f"index has {len(self._shas)}"
            )

        payload_end = len(pack_bytes) - 32
        expected_checksum = pack_bytes[payload_end:]
        actual_checksum = hashlib.sha256(pack_bytes[:payload_end]).digest()
        if actual_checksum != expected_checksum:
            raise ValueError("pack SHA-256 checksum mismatch")

        for offset in self._offsets.values():
            if offset < 12 or offset >= payload_end:
                raise ValueError(
                    f"pack index object offset {offset} is outside the pack payload"
                )

        self._pack_bytes = pack_bytes
        self._payload_end = payload_end
        return pack_bytes, payload_end

    def _entry_end(self, offset: int, payload_end: int) -> int:
        later_offsets = [candidate for candidate in self._offsets.values() if candidate > offset]
        return min(later_offsets) if later_offsets else payload_end

    def has_object(self, sha: str) -> bool:
        return sha in self._offsets

    def get_shas(self) -> List[str]:
        return list(self._shas)

    def read_object(self, sha: str) -> Optional[GitObject]:
        if sha not in self._offsets:
            return None

        pack_bytes, payload_end = self._load_pack_image()
        offset = self._offsets[sha]
        entry_end = self._entry_end(offset, payload_end)

        # Import the strict Phase 66 primitives lazily to avoid a module cycle:
        # pack_plumbing itself imports the pack type-id table above.
        from .pack_plumbing import _decompress_entry, _read_varint, _validate_store_bytes

        type_id, declared_size, compressed_pos = _read_varint(
            pack_bytes, offset, entry_end
        )
        type_name = _ID_TYPE_MAP.get(type_id)
        if type_name is None:
            raise ValueError(f"unsupported packed object type id: {type_id}")

        store_bytes, next_pos = _decompress_entry(
            pack_bytes,
            compressed_pos,
            entry_end,
            declared_size,
            offset,
        )
        if next_pos != entry_end:
            raise ValueError(
                f"pack entry at offset {offset} ends at {next_pos}, "
                f"but index boundary is {entry_end}"
            )

        actual_crc = zlib.crc32(pack_bytes[offset:entry_end]) & 0xFFFFFFFF
        expected_crc = self._crcs[sha]
        if actual_crc != expected_crc:
            raise ValueError(
                f"CRC-32 mismatch for object {sha} at offset {offset}"
            )

        actual_oid = _validate_store_bytes(store_bytes, type_name)
        if actual_oid != sha:
            raise ValueError(
                f"pack index object ID mismatch: requested {sha}, decoded {actual_oid}"
            )

        from .store import ObjectStore

        return ObjectStore._parse(store_bytes)
