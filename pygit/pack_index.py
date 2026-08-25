"""Strict parser for pygit's SHA-256 fan-out pack indexes.

The project uses a Git-inspired version-2 ``.idx`` layout whose object-name
section stores canonical 64-character SHA-256 hex strings.  This module is the
single validation boundary shared by inspection commands and :class:`PackReader`.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple


_MAGIC = b"\xfftOc"
_VERSION = 2
_FANOUT_ENTRIES = 256
_HEADER_SIZE = 8
_FANOUT_SIZE = _FANOUT_ENTRIES * 4
_FIXED_PREFIX = _HEADER_SIZE + _FANOUT_SIZE
_CHECKSUM_SIZE = 32
_OID_WIDTH = 64
_RECORD_WIDTH = _OID_WIDTH + 4 + 4
_HEX = frozenset("0123456789abcdef")


@dataclass(frozen=True)
class PackIndexEntry:
    """One object record from a validated pack index."""

    oid: str
    crc32: int
    offset: int


@dataclass(frozen=True)
class ParsedPackIndex:
    """A fully validated pygit pack-index image."""

    version: int
    checksum: str
    fanout: Tuple[int, ...]
    entries: Tuple[PackIndexEntry, ...]

    @property
    def object_count(self) -> int:
        return len(self.entries)


def _validate_fanout(fanout: Tuple[int, ...]) -> None:
    previous = 0
    for index, value in enumerate(fanout):
        if value < previous:
            raise ValueError(
                f"pack index fan-out table decreases at bucket {index:02x}"
            )
        previous = value


def _expected_fanout(oids: Tuple[str, ...]) -> Tuple[int, ...]:
    counts = [0] * _FANOUT_ENTRIES
    for oid in oids:
        counts[int(oid[:2], 16)] += 1
    cumulative = 0
    result = []
    for count in counts:
        cumulative += count
        result.append(cumulative)
    return tuple(result)


def parse_index_bytes(data: bytes) -> ParsedPackIndex:
    """Validate and decode one complete pygit ``.idx`` byte string."""
    minimum = _FIXED_PREFIX + _CHECKSUM_SIZE
    if len(data) < minimum:
        raise ValueError("pack index is too short")
    if data[:4] != _MAGIC:
        raise ValueError("invalid pack index signature")

    version = struct.unpack(">I", data[4:8])[0]
    if version != _VERSION:
        raise ValueError(f"unsupported pack index version: {version}")

    fanout = struct.unpack(">256I", data[_HEADER_SIZE:_FIXED_PREFIX])
    _validate_fanout(fanout)
    object_count = fanout[-1]
    expected_size = _FIXED_PREFIX + object_count * _RECORD_WIDTH + _CHECKSUM_SIZE
    if len(data) != expected_size:
        raise ValueError(
            f"pack index size mismatch: expected {expected_size} bytes for "
            f"{object_count} objects, got {len(data)}"
        )

    expected_checksum = data[-_CHECKSUM_SIZE:]
    actual_checksum = hashlib.sha256(data[:-_CHECKSUM_SIZE]).digest()
    if actual_checksum != expected_checksum:
        raise ValueError("pack index SHA-256 checksum mismatch")

    pos = _FIXED_PREFIX
    oids = []
    for index in range(object_count):
        raw = data[pos : pos + _OID_WIDTH]
        pos += _OID_WIDTH
        try:
            oid = raw.decode("ascii")
        except UnicodeDecodeError as exc:
            raise ValueError(f"pack index object {index} is not ASCII hex") from exc
        if len(oid) != _OID_WIDTH or any(char not in _HEX for char in oid):
            raise ValueError(
                f"pack index object {index} is not a canonical 64-hex SHA-256 ID"
            )
        oids.append(oid)

    oid_tuple = tuple(oids)
    if any(left >= right for left, right in zip(oid_tuple, oid_tuple[1:])):
        raise ValueError("pack index object IDs must be strictly increasing")
    if _expected_fanout(oid_tuple) != fanout:
        raise ValueError("pack index fan-out table does not match object IDs")

    crc_start = pos
    offset_start = crc_start + object_count * 4
    checksum_start = len(data) - _CHECKSUM_SIZE
    crcs = struct.unpack(
        f">{object_count}I", data[crc_start:offset_start]
    ) if object_count else ()
    offsets = struct.unpack(
        f">{object_count}I", data[offset_start:checksum_start]
    ) if object_count else ()

    if any(offset < 12 for offset in offsets):
        raise ValueError("pack index contains an object offset before the pack header")
    if len(set(offsets)) != len(offsets):
        raise ValueError("pack index contains duplicate object offsets")

    entries = tuple(
        PackIndexEntry(oid=oid, crc32=crc, offset=offset)
        for oid, crc, offset in zip(oid_tuple, crcs, offsets)
    )
    return ParsedPackIndex(
        version=version,
        checksum=actual_checksum.hex(),
        fanout=fanout,
        entries=entries,
    )


def parse_index(path: Path) -> ParsedPackIndex:
    """Read and validate a pygit pack index from *path*."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    return parse_index_bytes(path.read_bytes())
