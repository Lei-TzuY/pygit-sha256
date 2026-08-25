"""Low-level SHA-256 pack import and unpack plumbing.

This module intentionally targets pygit's own pack format from :mod:`pygit.pack`.
It validates a complete pack before producing an index or materializing loose
objects so malformed input cannot leave a half-imported repository.
"""

from __future__ import annotations

import hashlib
import os
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Tuple

from .pack import _ID_TYPE_MAP
from .repo import Repository
from .store import ObjectStore


@dataclass(frozen=True)
class PackEntry:
    """One validated object entry in a pygit pack."""

    oid: str
    type_name: str
    size: int
    compressed_size: int
    offset: int
    crc32: int
    store_bytes: bytes = field(repr=False)


@dataclass(frozen=True)
class ParsedPack:
    """Validated pack metadata and decoded object envelopes."""

    version: int
    checksum: str
    size: int
    entries: Tuple[PackEntry, ...]


@dataclass(frozen=True)
class IndexPackResult:
    pack_path: Path
    idx_path: Path
    checksum: str
    object_count: int
    oids: Tuple[str, ...]


@dataclass(frozen=True)
class UnpackResult:
    object_count: int
    written: int
    existing: int
    oids: Tuple[str, ...]


def _read_varint(data: bytes, pos: int, limit: int) -> tuple[int, int, int]:
    if pos >= limit:
        raise ValueError("truncated pack object header")
    first = data[pos]
    type_id = (first >> 4) & 0x07
    size = first & 0x0F
    shift = 4
    pos += 1
    while first & 0x80:
        if pos >= limit:
            raise ValueError("truncated pack object size")
        first = data[pos]
        size |= (first & 0x7F) << shift
        shift += 7
        if shift > 67:
            raise ValueError("pack object size varint is too large")
        pos += 1
    return type_id, size, pos


def _validate_store_bytes(store_bytes: bytes, expected_type: bytes) -> None:
    try:
        nul = store_bytes.index(b"\x00")
    except ValueError as exc:
        raise ValueError("packed object is missing its object envelope") from exc
    header = store_bytes[:nul]
    payload = store_bytes[nul + 1 :]
    try:
        type_name, size_text = header.split(b" ", 1)
        declared = int(size_text)
    except (ValueError, TypeError) as exc:
        raise ValueError("packed object has a malformed object envelope") from exc
    if type_name != expected_type:
        raise ValueError(
            f"pack type {expected_type.decode()} disagrees with object envelope "
            f"{type_name.decode('ascii', 'replace')}"
        )
    if declared != len(payload):
        raise ValueError(
            f"packed object payload size mismatch: header says {declared}, got {len(payload)}"
        )
    # Parse the payload as the concrete object type as a final structural check.
    ObjectStore._parse(store_bytes)


def parse_pack_bytes(data: bytes) -> ParsedPack:
    """Validate and decode a complete pygit pack byte string."""
    if len(data) < 44:
        raise ValueError("pack file is too short")
    if data[:4] != b"PACK":
        raise ValueError("invalid pack signature")
    version, count = struct.unpack(">II", data[4:12])
    if version != 2:
        raise ValueError(f"unsupported pack version: {version}")

    payload_end = len(data) - 32
    expected_checksum = data[payload_end:]
    actual_checksum = hashlib.sha256(data[:payload_end]).digest()
    if actual_checksum != expected_checksum:
        raise ValueError("pack SHA-256 checksum mismatch")

    entries = []
    seen = set()
    pos = 12
    for _ in range(count):
        offset = pos
        type_id, declared_size, compressed_pos = _read_varint(data, pos, payload_end)
        type_name = _ID_TYPE_MAP.get(type_id)
        if type_name is None:
            raise ValueError(f"unsupported packed object type id: {type_id}")

        decompressor = zlib.decompressobj()
        try:
            store_bytes = decompressor.decompress(data[compressed_pos:payload_end])
        except zlib.error as exc:
            raise ValueError(f"invalid zlib stream at pack offset {offset}") from exc
        if not decompressor.eof:
            raise ValueError(f"truncated zlib stream at pack offset {offset}")
        consumed = (payload_end - compressed_pos) - len(decompressor.unused_data)
        if consumed <= 0:
            raise ValueError(f"empty zlib stream at pack offset {offset}")
        pos = compressed_pos + consumed
        if len(store_bytes) != declared_size:
            raise ValueError(
                f"pack entry size mismatch at offset {offset}: "
                f"header says {declared_size}, got {len(store_bytes)}"
            )

        _validate_store_bytes(store_bytes, type_name)
        oid = hashlib.sha256(store_bytes).hexdigest()
        if oid in seen:
            raise ValueError(f"duplicate object {oid} in pack")
        seen.add(oid)
        crc = zlib.crc32(data[offset:pos]) & 0xFFFFFFFF
        entries.append(
            PackEntry(
                oid=oid,
                type_name=type_name.decode("ascii"),
                size=declared_size,
                compressed_size=pos - offset,
                offset=offset,
                crc32=crc,
                store_bytes=store_bytes,
            )
        )

    if pos != payload_end:
        raise ValueError(
            f"pack contains {payload_end - pos} trailing byte(s) before its checksum"
        )
    if len(entries) != count:
        raise ValueError("pack object count mismatch")

    return ParsedPack(
        version=version,
        checksum=actual_checksum.hex(),
        size=len(data),
        entries=tuple(entries),
    )


def parse_pack(path: Path) -> ParsedPack:
    """Read and validate a pygit pack file."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    return parse_pack_bytes(path.read_bytes())


def build_index_bytes(pack: ParsedPack) -> bytes:
    """Build the project's fan-out ``.idx`` representation for *pack*."""
    entries = sorted(pack.entries, key=lambda item: item.oid)
    out = bytearray(b"\xfftOc" + struct.pack(">I", 2))

    counts = [0] * 256
    for entry in entries:
        counts[int(entry.oid[:2], 16)] += 1
    cumulative = 0
    for value in counts:
        cumulative += value
        out.extend(struct.pack(">I", cumulative))

    for entry in entries:
        out.extend(entry.oid.encode("ascii"))
    for entry in entries:
        out.extend(struct.pack(">I", entry.crc32))
    for entry in entries:
        if entry.offset > 0xFFFFFFFF:
            raise ValueError("pack offset exceeds the current 32-bit index format")
        out.extend(struct.pack(">I", entry.offset))

    out.extend(hashlib.sha256(out).digest())
    return bytes(out)


def index_pack(pack_path: Path, *, force: bool = False) -> IndexPackResult:
    """Validate *pack_path* and create/rebuild its sibling ``.idx`` file."""
    pack_path = Path(pack_path)
    pack = parse_pack(pack_path)
    idx_path = pack_path.with_suffix(".idx")
    if idx_path.exists() and not force:
        raise FileExistsError(f"index already exists: {idx_path}")

    data = build_index_bytes(pack)
    tmp = idx_path.with_name(idx_path.name + ".tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, idx_path)
    finally:
        if tmp.exists():
            tmp.unlink()

    return IndexPackResult(
        pack_path=pack_path,
        idx_path=idx_path,
        checksum=pack.checksum,
        object_count=len(pack.entries),
        oids=tuple(entry.oid for entry in pack.entries),
    )


def _validate_existing_loose(repo: Repository, entry: PackEntry) -> bool:
    path = repo.store.root / entry.oid[:2] / entry.oid[2:]
    if not path.exists():
        return False
    try:
        stored = zlib.decompress(path.read_bytes())
    except zlib.error as exc:
        raise ValueError(f"existing loose object {entry.oid} is corrupt") from exc
    if hashlib.sha256(stored).hexdigest() != entry.oid:
        raise ValueError(f"existing loose object {entry.oid} has the wrong hash")
    return True


def unpack_objects(repo: Repository, pack_path: Path, *, dry_run: bool = False) -> UnpackResult:
    """Validate a pack and materialize each object as a loose object.

    The complete input pack and every pre-existing loose object are validated
    before the first new loose object is written.
    """
    pack = parse_pack(Path(pack_path))
    existing = {entry.oid for entry in pack.entries if _validate_existing_loose(repo, entry)}
    pending = [entry for entry in pack.entries if entry.oid not in existing]

    if not dry_run:
        for entry in pending:
            path = repo.store.root / entry.oid[:2] / entry.oid[2:]
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            try:
                tmp.write_bytes(zlib.compress(entry.store_bytes))
                os.replace(tmp, path)
            finally:
                if tmp.exists():
                    tmp.unlink()

    return UnpackResult(
        object_count=len(pack.entries),
        written=0 if dry_run else len(pending),
        existing=len(existing),
        oids=tuple(entry.oid for entry in pack.entries),
    )
