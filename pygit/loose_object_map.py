"""Git-compatible loose-object SHA-256/SHA-1 mapping files.

Git 2.54 documents ``$GIT_DIR/objects/object-map/map-*.map`` (LMAP v1) as
the loose-object mapping format used with ``extensions.compatObjectFormat``.
This module writes that format with SHA-256 as the repository/storage format
and SHA-1 as the compatibility/native-remote format.
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


def encode_loose_object_map(native_to_local: Mapping[str, str]) -> bytes:
    """Encode verified SHA-1 -> SHA-256 identities as native Git LMAP v1 bytes.

    The first object format is SHA-256 (the repository/storage algorithm); the
    second is SHA-1 (the compatibility/native-remote algorithm). Every entry is
    marked as a loose-object mapping (metadata type 1). The trailer is the SHA-256
    digest of every preceding byte, exactly as required for a SHA-256 repository.
    """

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

    # Git's first table is ordered by the main/storage algorithm.
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
    # Full compatibility names stay in first-table order.
    for native, _ in storage_pairs:
        out += native
    storage_index = {local: index for index, (_, local) in enumerate(storage_pairs)}
    for _, local in compat_pairs:
        out += storage_index[local].to_bytes(4, "big")

    if len(out) != trailer_offset:
        raise RuntimeError("internal LMAP offset calculation mismatch")
    out += hashlib.sha256(out).digest()
    return bytes(out)


def publish_staged_loose_object_map(
    repo: Repository,
    staged: StagedPackfileUriImport,
) -> PublishedLooseObjectMap:
    """Atomically publish Phase321's verified mapping as an immutable LMAP file.

    Local object bytes are re-read before publication so every mapped SHA-256 is
    still a valid content-addressed object visible to this repository. The map
    file name is derived from its SHA-256 trailer and an identical pre-existing
    file is accepted idempotently. A conflicting file at that content-addressed
    path fails closed.
    """

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
        # Do not replace an independently published map: identical content is
        # idempotent; a different file at this checksum-derived path is corruption.
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
