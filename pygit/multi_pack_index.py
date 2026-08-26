"""Git-inspired multi-pack-index support for pygit's SHA-256 pack store.

The on-disk file lives at ``.pygit/objects/pack/multi-pack-index`` and uses a
small, inspectable chunk table inspired by Git's MIDX format.  Object IDs are
stored as raw 32-byte SHA-256 names even though pygit's per-pack ``.idx`` files
use 64-character ASCII hex object IDs.
"""

from __future__ import annotations

import bisect
import hashlib
import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

from .pack_index import ParsedPackIndex, parse_index


_MAGIC = b"MIDX"
_VERSION = 1
_HASH_VERSION_SHA256 = 2
_CHUNK_COUNT = 4
_BASE_MIDX_COUNT = 0
_CHECKSUM_SIZE = 32
_HEADER = struct.Struct(">4sBBBBI")
_CHUNK_ENTRY = struct.Struct(">4sQ")
_CHUNK_IDS = (b"PNAM", b"OIDF", b"OIDL", b"OOFF")
_TERMINATOR = b"\x00\x00\x00\x00"
_FANOUT_SIZE = 256 * 4
_RAW_OID_SIZE = 32
_OFFSET_RECORD_SIZE = 8
_HEX = frozenset("0123456789abcdef")


@dataclass(frozen=True)
class MultiPackIndexEntry:
    """One object-to-pack mapping from a validated multi-pack-index."""

    oid: str
    pack_name: str
    offset: int


@dataclass(frozen=True)
class ParsedMultiPackIndex:
    """A fully decoded pygit multi-pack-index image."""

    version: int
    hash_version: int
    checksum: str
    pack_names: Tuple[str, ...]
    fanout: Tuple[int, ...]
    entries: Tuple[MultiPackIndexEntry, ...]

    @property
    def object_count(self) -> int:
        return len(self.entries)

    def lookup(self, oid: str) -> Optional[MultiPackIndexEntry]:
        """Return the selected pack mapping for canonical 64-hex *oid*."""
        oid = oid.lower()
        if len(oid) != 64 or any(char not in _HEX for char in oid):
            return None
        names = [entry.oid for entry in self.entries]
        index = bisect.bisect_left(names, oid)
        if index < len(self.entries) and self.entries[index].oid == oid:
            return self.entries[index]
        return None


def _validate_pack_name(name: str) -> None:
    if (
        not name
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or "\x00" in name
        or not name.endswith(".idx")
    ):
        raise ValueError(f"invalid multi-pack-index pack name: {name!r}")
    try:
        name.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("multi-pack-index pack names must be ASCII") from exc


def _encode_pack_names(pack_names: Tuple[str, ...]) -> bytes:
    for name in pack_names:
        _validate_pack_name(name)
    if tuple(sorted(pack_names)) != pack_names or len(set(pack_names)) != len(pack_names):
        raise ValueError("multi-pack-index pack names must be unique and sorted")
    if not pack_names:
        return b""
    data = b"\x00".join(name.encode("ascii") for name in pack_names) + b"\x00"
    return data + b"\x00" * ((-len(data)) % 4)


def _decode_pack_names(data: bytes, pack_count: int) -> Tuple[str, ...]:
    if pack_count == 0:
        if data:
            raise ValueError("multi-pack-index has pack names but pack count is zero")
        return ()
    trimmed = data.rstrip(b"\x00")
    if not trimmed:
        raise ValueError("multi-pack-index pack-name chunk is empty")
    raw_names = trimmed.split(b"\x00")
    if len(raw_names) != pack_count:
        raise ValueError(
            f"multi-pack-index pack count mismatch: header has {pack_count}, "
            f"PNAM has {len(raw_names)}"
        )
    try:
        names = tuple(raw.decode("ascii") for raw in raw_names)
    except UnicodeDecodeError as exc:
        raise ValueError("multi-pack-index pack names must be ASCII") from exc
    for name in names:
        _validate_pack_name(name)
    if tuple(sorted(names)) != names or len(set(names)) != len(names):
        raise ValueError("multi-pack-index pack names must be unique and sorted")
    if _encode_pack_names(names) != data:
        raise ValueError("multi-pack-index pack-name chunk has non-canonical padding")
    return names


def _expected_fanout(oids: Tuple[str, ...]) -> Tuple[int, ...]:
    counts = [0] * 256
    for oid in oids:
        counts[int(oid[:2], 16)] += 1
    cumulative = 0
    result = []
    for count in counts:
        cumulative += count
        result.append(cumulative)
    return tuple(result)


def _build_bytes(
    pack_names: Tuple[str, ...],
    selected: Dict[str, Tuple[int, int]],
) -> bytes:
    pnam = _encode_pack_names(pack_names)
    oids = tuple(sorted(selected))
    fanout = _expected_fanout(oids)
    oidf = struct.pack(">256I", *fanout)
    oidl = b"".join(bytes.fromhex(oid) for oid in oids)
    ooff = b"".join(struct.pack(">II", selected[oid][0], selected[oid][1]) for oid in oids)

    table_size = (_CHUNK_COUNT + 1) * _CHUNK_ENTRY.size
    cursor = _HEADER.size + table_size
    chunk_offsets = []
    chunks = (pnam, oidf, oidl, ooff)
    for chunk in chunks:
        chunk_offsets.append(cursor)
        cursor += len(chunk)
    end_offset = cursor

    data = bytearray(
        _HEADER.pack(
            _MAGIC,
            _VERSION,
            _HASH_VERSION_SHA256,
            _CHUNK_COUNT,
            _BASE_MIDX_COUNT,
            len(pack_names),
        )
    )
    for chunk_id, offset in zip(_CHUNK_IDS, chunk_offsets):
        data.extend(_CHUNK_ENTRY.pack(chunk_id, offset))
    data.extend(_CHUNK_ENTRY.pack(_TERMINATOR, end_offset))
    for chunk in chunks:
        data.extend(chunk)
    data.extend(hashlib.sha256(data).digest())
    return bytes(data)


def parse_multi_pack_index_bytes(data: bytes) -> ParsedMultiPackIndex:
    """Strictly validate and decode a complete pygit multi-pack-index image."""
    table_size = (_CHUNK_COUNT + 1) * _CHUNK_ENTRY.size
    minimum = _HEADER.size + table_size + _CHECKSUM_SIZE
    if len(data) < minimum:
        raise ValueError("multi-pack-index is too short")

    checksum_start = len(data) - _CHECKSUM_SIZE
    expected_checksum = data[checksum_start:]
    actual_checksum = hashlib.sha256(data[:checksum_start]).digest()
    if expected_checksum != actual_checksum:
        raise ValueError("multi-pack-index SHA-256 checksum mismatch")

    magic, version, hash_version, chunk_count, base_count, pack_count = _HEADER.unpack(
        data[: _HEADER.size]
    )
    if magic != _MAGIC:
        raise ValueError("invalid multi-pack-index signature")
    if version != _VERSION:
        raise ValueError(f"unsupported multi-pack-index version: {version}")
    if hash_version != _HASH_VERSION_SHA256:
        raise ValueError(f"unsupported multi-pack-index hash version: {hash_version}")
    if chunk_count != _CHUNK_COUNT:
        raise ValueError(f"unsupported multi-pack-index chunk count: {chunk_count}")
    if base_count != _BASE_MIDX_COUNT:
        raise ValueError("base multi-pack-index files are not supported")

    records = []
    pos = _HEADER.size
    for _ in range(_CHUNK_COUNT + 1):
        records.append(_CHUNK_ENTRY.unpack(data[pos : pos + _CHUNK_ENTRY.size]))
        pos += _CHUNK_ENTRY.size
    if tuple(record[0] for record in records[:_CHUNK_COUNT]) != _CHUNK_IDS:
        raise ValueError("multi-pack-index has an invalid chunk table")
    if records[-1][0] != _TERMINATOR:
        raise ValueError("multi-pack-index chunk table is missing its terminator")

    offsets = tuple(record[1] for record in records)
    first_chunk = _HEADER.size + table_size
    if offsets[0] != first_chunk or offsets[-1] != checksum_start:
        raise ValueError("multi-pack-index chunk offsets do not cover the payload")
    if any(left > right for left, right in zip(offsets, offsets[1:])):
        raise ValueError("multi-pack-index chunk offsets are not monotonic")
    if any(offset < first_chunk or offset > checksum_start for offset in offsets):
        raise ValueError("multi-pack-index chunk offset is outside the payload")

    pnam_start, oidf_start, oidl_start, ooff_start, end_offset = offsets
    pnam = data[pnam_start:oidf_start]
    oidf = data[oidf_start:oidl_start]
    oidl = data[oidl_start:ooff_start]
    ooff = data[ooff_start:end_offset]

    pack_names = _decode_pack_names(pnam, pack_count)
    if len(oidf) != _FANOUT_SIZE:
        raise ValueError("multi-pack-index fan-out chunk has the wrong size")
    fanout = struct.unpack(">256I", oidf)
    previous = 0
    for bucket, value in enumerate(fanout):
        if value < previous:
            raise ValueError(
                f"multi-pack-index fan-out table decreases at bucket {bucket:02x}"
            )
        previous = value
    object_count = fanout[-1]
    if len(oidl) != object_count * _RAW_OID_SIZE:
        raise ValueError("multi-pack-index object-name chunk has the wrong size")
    if len(ooff) != object_count * _OFFSET_RECORD_SIZE:
        raise ValueError("multi-pack-index object-offset chunk has the wrong size")

    oids = tuple(
        oidl[index : index + _RAW_OID_SIZE].hex()
        for index in range(0, len(oidl), _RAW_OID_SIZE)
    )
    if any(left >= right for left, right in zip(oids, oids[1:])):
        raise ValueError("multi-pack-index object IDs must be strictly increasing")
    if _expected_fanout(oids) != fanout:
        raise ValueError("multi-pack-index fan-out table does not match object IDs")

    entries = []
    for index, oid in enumerate(oids):
        pack_id, offset = struct.unpack(
            ">II", ooff[index * _OFFSET_RECORD_SIZE : (index + 1) * _OFFSET_RECORD_SIZE]
        )
        if pack_id >= len(pack_names):
            raise ValueError(
                f"multi-pack-index object {oid} references invalid pack id {pack_id}"
            )
        if offset < 12:
            raise ValueError(
                f"multi-pack-index object {oid} has an offset before the pack header"
            )
        entries.append(
            MultiPackIndexEntry(oid=oid, pack_name=pack_names[pack_id], offset=offset)
        )

    return ParsedMultiPackIndex(
        version=version,
        hash_version=hash_version,
        checksum=actual_checksum.hex(),
        pack_names=pack_names,
        fanout=fanout,
        entries=tuple(entries),
    )


def parse_multi_pack_index(path: Path) -> ParsedMultiPackIndex:
    """Read and strictly parse *path*."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    return parse_multi_pack_index_bytes(path.read_bytes())


def write_multi_pack_index(pack_dir: Path) -> Path:
    """Create/update ``multi-pack-index`` for all current ``*.idx`` files."""
    pack_dir = Path(pack_dir)
    pack_dir.mkdir(parents=True, exist_ok=True)
    idx_paths = sorted(pack_dir.glob("*.idx"), key=lambda path: path.name)
    if not idx_paths:
        raise ValueError("cannot write multi-pack-index without pack indexes")

    pack_names = tuple(path.name for path in idx_paths)
    _encode_pack_names(pack_names)
    selected: Dict[str, Tuple[int, int]] = {}
    for pack_id, idx_path in enumerate(idx_paths):
        pack_path = idx_path.with_suffix(".pack")
        if not pack_path.is_file():
            raise FileNotFoundError(pack_path)
        index: ParsedPackIndex = parse_index(idx_path)
        for entry in index.entries:
            # A duplicate object may legitimately be present in multiple packs.
            # Select the lexicographically first pack deterministically.
            selected.setdefault(entry.oid, (pack_id, entry.offset))

    data = _build_bytes(pack_names, selected)
    output = pack_dir / "multi-pack-index"
    temporary = output.with_name(output.name + ".tmp")
    temporary.write_bytes(data)
    os.replace(str(temporary), str(output))
    return output


def verify_multi_pack_index(path: Path) -> ParsedMultiPackIndex:
    """Verify MIDX structure and its mappings against current source indexes."""
    path = Path(path)
    parsed = parse_multi_pack_index(path)
    pack_dir = path.parent

    source: Dict[str, Dict[str, int]] = {}
    union = set()
    for pack_name in parsed.pack_names:
        idx_path = pack_dir / pack_name
        pack_path = idx_path.with_suffix(".pack")
        if not pack_path.is_file():
            raise FileNotFoundError(pack_path)
        index = parse_index(idx_path)
        mapping = {entry.oid: entry.offset for entry in index.entries}
        source[pack_name] = mapping
        union.update(mapping)

    indexed = {entry.oid for entry in parsed.entries}
    if indexed != union:
        raise ValueError(
            "multi-pack-index object set does not match its source pack indexes"
        )
    for entry in parsed.entries:
        actual_offset = source[entry.pack_name].get(entry.oid)
        if actual_offset != entry.offset:
            raise ValueError(
                f"multi-pack-index mapping mismatch for object {entry.oid}"
            )
    return parsed
