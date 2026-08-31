"""Git-compatible loose-object SHA-256/SHA-1 mapping files.

Git 2.54 documents ``$GIT_DIR/objects/object-map/map-*.map`` (LMAP v1) as
the loose-object mapping format used with ``extensions.compatObjectFormat``.
This module reads and writes that format with SHA-256 as the repository/storage
format and SHA-1 as the compatibility/native-remote format.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .protocol_v2_packfile_uri_stage import StagedPackfileUriImport
from .repo import Repository

_SHA256_FORMAT_ID = 0x73323536  # "s256"
_SHA1_FORMAT_ID = 0x73686131  # "sha1"
_HEADER_SIZE = 60
_MAP_TYPE_LOOSE_OBJECT = 1


@dataclass(frozen=True)
class PublishedLooseObjectMap:
    """One immutable Git LMAP v1 file published in ``objects/object-map``."""

    path: Path
    checksum: str
    object_count: int


@dataclass(frozen=True)
class LooseObjectMap:
    """Validated contents of one Git LMAP v1 loose-object mapping file."""

    native_to_local: Mapping[str, str]
    checksum: str
    object_count: int


def _raw_oid(value: str, hex_len: int, label: str) -> bytes:
    if not isinstance(value, str) or len(value) != hex_len or value != value.lower():
        raise ValueError(f"{label} must be a full lowercase {hex_len}-hex object id")
    try:
        raw = bytes.fromhex(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be hexadecimal") from exc
    return raw


def _short_len(values: list[bytes]) -> int:
    """Match Git's minimum byte-prefix length for a sorted object-name table."""

    if len(values) <= 1:
        return 1
    needed = 1
    for left, right in zip(values, values[1:]):
        common = 0
        for a, b in zip(left, right):
            if a != b:
                break
            common += 1
        needed = max(needed, common + 1)
    return min(needed, len(values[0]))


def _padding(nitems: int, short_len: int) -> bytes:
    count = (4 - ((nitems * short_len) & 3)) & 3
    return b"\0" * count


def _u32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 4], "big")


def _u64(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 8], "big")


def encode_loose_object_map(native_to_local: Mapping[str, str]) -> bytes:
    """Encode verified SHA-1 -> SHA-256 identities as native Git LMAP v1 bytes."""

    if not isinstance(native_to_local, Mapping):
        raise TypeError("loose object map requires a native-to-local mapping")
    if not native_to_local:
        raise ValueError("loose object map requires at least one object mapping")

    pairs: list[tuple[bytes, bytes]] = []
    seen_local: dict[bytes, bytes] = {}
    for native_hex, local_hex in native_to_local.items():
        native = _raw_oid(native_hex, 40, "native SHA-1")
        local = _raw_oid(local_hex, 64, "local SHA-256")
        previous_native = seen_local.get(local)
        if previous_native is not None and previous_native != native:
            raise ValueError("one local SHA-256 cannot map to multiple native SHA-1 ids")
        seen_local[local] = native
        pairs.append((native, local))

    storage_pairs = sorted(pairs, key=lambda pair: pair[1])
    compat_pairs = sorted(pairs, key=lambda pair: pair[0])
    storage_oids = [local for _, local in storage_pairs]
    compat_oids = [native for native, _ in compat_pairs]
    storage_short = _short_len(storage_oids)
    compat_short = _short_len(compat_oids)
    storage_pad = _padding(len(pairs), storage_short)
    compat_pad = _padding(len(pairs), compat_short)

    storage_data_offset = _HEADER_SIZE + len(storage_pad)
    storage_section_len = len(pairs) * (storage_short + 32 + 4)
    compat_data_offset = storage_data_offset + storage_section_len + len(compat_pad)
    compat_section_len = len(pairs) * (compat_short + 20 + 4)
    trailer_offset = compat_data_offset + compat_section_len

    out = bytearray()
    out += b"LMAP"
    out += (1).to_bytes(4, "big")
    out += _HEADER_SIZE.to_bytes(4, "big")
    out += len(pairs).to_bytes(4, "big")
    out += (2).to_bytes(4, "big")
    out += _SHA256_FORMAT_ID.to_bytes(4, "big")
    out += storage_short.to_bytes(4, "big")
    out += storage_data_offset.to_bytes(8, "big")
    out += _SHA1_FORMAT_ID.to_bytes(4, "big")
    out += compat_short.to_bytes(4, "big")
    out += compat_data_offset.to_bytes(8, "big")
    out += trailer_offset.to_bytes(8, "big")
    assert len(out) == _HEADER_SIZE

    out += storage_pad
    for oid in storage_oids:
        out += oid[:storage_short]
    for oid in storage_oids:
        out += oid
    for _ in storage_pairs:
        out += _MAP_TYPE_LOOSE_OBJECT.to_bytes(4, "big")

    out += compat_pad
    for oid in compat_oids:
        out += oid[:compat_short]
    for native, _ in storage_pairs:
        out += native
    storage_index = {local: index for index, (_, local) in enumerate(storage_pairs)}
    for _, local in compat_pairs:
        out += storage_index[local].to_bytes(4, "big")

    if len(out) != trailer_offset:
        raise RuntimeError("internal LMAP offset calculation mismatch")
    out += hashlib.sha256(out).digest()
    return bytes(out)


def decode_loose_object_map(data: bytes) -> LooseObjectMap:
    """Validate and decode one Git LMAP v1 SHA-256/SHA-1 loose-object map.

    The decoder validates the content checksum, exact algorithm identifiers,
    section offsets, shortened-name tables, sort order, metadata type, and the
    compatibility-order permutation before exposing any identity mapping.
    """

    if not isinstance(data, bytes):
        raise TypeError("loose object map data must be bytes")
    if len(data) < _HEADER_SIZE + 32:
        raise ValueError("loose object map is truncated")
    if data[:4] != b"LMAP" or _u32(data, 4) != 1:
        raise ValueError("unsupported loose object map signature or version")
    if _u32(data, 8) != _HEADER_SIZE or _u32(data, 16) != 2:
        raise ValueError("unsupported loose object map header")

    count = _u32(data, 12)
    if count < 1:
        raise ValueError("loose object map must contain at least one object")
    if _u32(data, 20) != _SHA256_FORMAT_ID or _u32(data, 36) != _SHA1_FORMAT_ID:
        raise ValueError("loose object map must map SHA-256 storage to SHA-1 compatibility ids")

    storage_short = _u32(data, 24)
    compat_short = _u32(data, 40)
    storage_offset = _u64(data, 28)
    compat_offset = _u64(data, 44)
    trailer_offset = _u64(data, 52)
    if not (1 <= storage_short <= 32 and 1 <= compat_short <= 20):
        raise ValueError("invalid shortened object-name width")
    if trailer_offset + 32 != len(data):
        raise ValueError("invalid loose object map trailer offset")
    if hashlib.sha256(data[:trailer_offset]).digest() != data[trailer_offset:]:
        raise ValueError("loose object map checksum mismatch")

    storage_full = storage_offset + count * storage_short
    storage_meta = storage_full + count * 32
    storage_end = storage_meta + count * 4
    compat_full = compat_offset + count * compat_short
    compat_order = compat_full + count * 20
    compat_end = compat_order + count * 4
    if not (_HEADER_SIZE <= storage_offset <= storage_full <= storage_meta <= storage_end):
        raise ValueError("invalid SHA-256 table offsets")
    if not (storage_end <= compat_offset <= compat_full <= compat_order <= compat_end):
        raise ValueError("invalid SHA-1 table offsets")
    if compat_end != trailer_offset:
        raise ValueError("loose object map sections do not end at trailer")
    if data[_HEADER_SIZE:storage_offset] != _padding(count, storage_short):
        raise ValueError("invalid SHA-256 table padding")
    if data[storage_end:compat_offset] != _padding(count, compat_short):
        raise ValueError("invalid SHA-1 table padding")

    storage_oids = [data[storage_full + i * 32 : storage_full + (i + 1) * 32] for i in range(count)]
    if storage_oids != sorted(storage_oids) or len(set(storage_oids)) != count:
        raise ValueError("SHA-256 object table is not strictly sorted")
    storage_prefixes = [
        data[storage_offset + i * storage_short : storage_offset + (i + 1) * storage_short]
        for i in range(count)
    ]
    if storage_prefixes != [oid[:storage_short] for oid in storage_oids]:
        raise ValueError("SHA-256 shortened-name table does not match full object ids")
    if storage_short != _short_len(storage_oids):
        raise ValueError("SHA-256 shortened-name width is not canonical")

    metadata = [_u32(data, storage_meta + i * 4) for i in range(count)]
    if any(value != _MAP_TYPE_LOOSE_OBJECT for value in metadata):
        raise ValueError("unsupported loose object map metadata type")

    native_by_storage = [
        data[compat_full + i * 20 : compat_full + (i + 1) * 20] for i in range(count)
    ]
    if len(set(native_by_storage)) != count:
        raise ValueError("duplicate SHA-1 compatibility object id")
    order = [_u32(data, compat_order + i * 4) for i in range(count)]
    if sorted(order) != list(range(count)):
        raise ValueError("invalid SHA-1 compatibility order table")
    native_sorted = [native_by_storage[index] for index in order]
    if native_sorted != sorted(native_sorted):
        raise ValueError("SHA-1 compatibility object table is not strictly sorted")
    compat_prefixes = [
        data[compat_offset + i * compat_short : compat_offset + (i + 1) * compat_short]
        for i in range(count)
    ]
    if compat_prefixes != [oid[:compat_short] for oid in native_sorted]:
        raise ValueError("SHA-1 shortened-name table does not match full object ids")
    if compat_short != _short_len(native_sorted):
        raise ValueError("SHA-1 shortened-name width is not canonical")

    native_to_local = {
        native_by_storage[i].hex(): storage_oids[i].hex() for i in range(count)
    }
    return LooseObjectMap(native_to_local, data[trailer_offset:].hex(), count)


def read_loose_object_maps(repo: Repository) -> tuple[LooseObjectMap, ...]:
    """Read every immutable Git LMAP file and reject cross-file contradictions."""

    if not isinstance(repo, Repository):
        raise TypeError("loose object map lookup requires a Repository")
    directory = repo.pygit_dir / "objects" / "object-map"
    if not directory.exists():
        return ()

    maps: list[LooseObjectMap] = []
    native_seen: dict[str, str] = {}
    local_seen: dict[str, str] = {}
    for path in sorted(directory.glob("map-*.map")):
        decoded = decode_loose_object_map(path.read_bytes())
        if path.name != f"map-{decoded.checksum}.map":
            raise ValueError("loose object map filename does not match content checksum")
        for native, local in decoded.native_to_local.items():
            previous_local = native_seen.get(native)
            if previous_local is not None and previous_local != local:
                raise ValueError("native SHA-1 maps to conflicting local SHA-256 ids")
            previous_native = local_seen.get(local)
            if previous_native is not None and previous_native != native:
                raise ValueError("local SHA-256 maps to conflicting native SHA-1 ids")
            native_seen[native] = local
            local_seen[local] = native
        maps.append(decoded)
    return tuple(maps)


def lookup_native_sha1(repo: Repository, local_sha256: str) -> str | None:
    """Return the verified compatibility SHA-1 for one full local SHA-256 id."""

    _raw_oid(local_sha256, 64, "local SHA-256")
    match: str | None = None
    for object_map in read_loose_object_maps(repo):
        for native, local in object_map.native_to_local.items():
            if local != local_sha256:
                continue
            if match is not None and match != native:
                raise ValueError("local SHA-256 has conflicting native SHA-1 mappings")
            match = native
    return match


def lookup_local_sha256(repo: Repository, native_sha1: str) -> str | None:
    """Return the verified local SHA-256 for one full compatibility SHA-1 id."""

    _raw_oid(native_sha1, 40, "native SHA-1")
    match: str | None = None
    for object_map in read_loose_object_maps(repo):
        local = object_map.native_to_local.get(native_sha1)
        if local is None:
            continue
        if match is not None and match != local:
            raise ValueError("native SHA-1 has conflicting local SHA-256 mappings")
        match = local
    return match


def publish_staged_loose_object_map(
    repo: Repository,
    staged: StagedPackfileUriImport,
) -> PublishedLooseObjectMap:
    """Atomically publish Phase321's verified mapping as an immutable LMAP file."""

    if not isinstance(repo, Repository):
        raise TypeError("loose object map publication requires a Repository")
    if not isinstance(staged, StagedPackfileUriImport):
        raise TypeError("loose object map publication requires a staged import")

    for local_oid in staged.native_to_local.values():
        raw = repo.store.read_store_bytes(local_oid)
        if hashlib.sha256(raw).hexdigest() != local_oid:
            raise ValueError("staged local SHA-256 no longer matches stored object content")

    encoded = encode_loose_object_map(staged.native_to_local)
    checksum = encoded[-32:].hex()
    if hashlib.sha256(encoded[:-32]).hexdigest() != checksum:
        raise RuntimeError("internal LMAP trailer checksum mismatch")

    directory = repo.pygit_dir / "objects" / "object-map"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"map-{checksum}.map"
    if target.exists():
        if target.read_bytes() != encoded:
            raise RuntimeError("content-addressed loose object map path contains different bytes")
        return PublishedLooseObjectMap(target, checksum, len(staged.native_to_local))

    fd, temp_name = tempfile.mkstemp(prefix=".tmp-object-map-", dir=str(directory))
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp, target)
        except FileExistsError:
            if target.read_bytes() != encoded:
                raise RuntimeError(
                    "content-addressed loose object map path contains different bytes"
                )
        finally:
            temp.unlink(missing_ok=True)
    finally:
        temp.unlink(missing_ok=True)

    return PublishedLooseObjectMap(target, checksum, len(staged.native_to_local))
