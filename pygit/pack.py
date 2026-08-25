"""
pygit/pack.py
=============
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

import os
import struct
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

    def write_pack_and_idx(self, output_dir: Path, name_prefix: str = "pack") -> Tuple[Path, Path]:
        output_dir.mkdir(parents=True, exist_ok=True)

        # Build .pack binary stream
        pack_data = bytearray()
        pack_data.extend(b"PACK")
        pack_data.extend(struct.pack(">II", 2, len(self.objects)))

        offsets: List[int] = []
        crcs: List[int] = []
        shas: List[str] = []

        import hashlib

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

        pack_path.write_bytes(pack_data)

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

        idx_path.write_bytes(idx_data)

        return pack_path, idx_path


class PackReader:
    """Reads objects from a paired .pack and .idx file."""

    def __init__(self, idx_path: Path) -> None:
        self.idx_path = idx_path
        self.pack_path = idx_path.with_suffix(".pack")
        self._shas: List[str] = []
        self._offsets: Dict[str, int] = {}
        self._load_idx()

    def _load_idx(self) -> None:
        if not self.idx_path.exists() or not self.pack_path.exists():
            return

        from .pack_index import parse_index

        index = parse_index(self.idx_path)
        self._shas = [entry.oid for entry in index.entries]
        self._offsets = {entry.oid: entry.offset for entry in index.entries}

    def has_object(self, sha: str) -> bool:
        return sha in self._offsets

    def get_shas(self) -> List[str]:
        return list(self._shas)

    def read_object(self, sha: str) -> Optional[GitObject]:
        if sha not in self._offsets:
            return None

        offset = self._offsets[sha]
        pack_bytes = self.pack_path.read_bytes()

        # Parse varint header
        pos = offset
        first = pack_bytes[pos]
        type_id = (first >> 4) & 0x07
        size = first & 0x0F
        shift = 4
        pos += 1
        while first & 0x80:
            first = pack_bytes[pos]
            size |= (first & 0x7F) << shift
            shift += 7
            pos += 1

        # Decompress object payload
        compressed = pack_bytes[pos:]
        store_bytes = zlib.decompress(compressed)

        type_name = _ID_TYPE_MAP.get(type_id, b"blob")
        klass = {
            b"blob": BlobObject,
            b"tree": TreeObject,
            b"commit": CommitObject,
            b"tag": TagObject,
        }.get(type_name, BlobObject)

        obj = klass.__new__(klass)
        obj.deserialize(store_bytes[store_bytes.index(b"\x00") + 1:])
        return obj
