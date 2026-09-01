"""Strict, read-only Git bundle v2/v3 payload verification.

Bundle URIs are an optional bootstrap optimization.  A downloaded payload must
not become trusted repository state merely because its HTTP transfer succeeded.
This module establishes the file-format trust boundary: bundle headers are
validated in the remote-native SHA-1 domain and the embedded pack is verified
structurally before any later import/ref publication phase may consume it.
"""

from __future__ import annotations

import hashlib
import re
import struct
import zlib
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from .ref_query import check_ref_format
from .remote import NativeObject, PackParser


_HEX40_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_CAPABILITY_KEY_RE = re.compile(r"^[A-Za-z0-9-]+$")


@dataclass(frozen=True)
class BundlePrerequisite:
    """One remote-native object the bundle intentionally does not contain."""

    oid: str
    comment: bytes


@dataclass(frozen=True)
class GitBundlePayload:
    """One verified Git bundle payload without repository mutation.

    ``objects`` is populated whenever the pack can be expanded without external
    prerequisite bases.  A v3 ``filter`` capability is preserved separately:
    those contained objects may parse successfully while the reachable graph is
    deliberately incomplete.  Bundles with prerequisites are commonly thin
    packs whose REF_DELTA bases live in the receiver; their pack bytes are fully
    envelope/entry/checksum verified here, but graph expansion deliberately
    waits for a later prerequisite-aware import boundary.
    """

    version: int
    object_format: str
    capabilities: Dict[str, Optional[bytes]]
    prerequisites: Tuple[BundlePrerequisite, ...]
    refs: Dict[str, str]
    filter_spec: Optional[str]
    pack: bytes
    pack_version: int
    pack_entries: int
    objects: Optional[Dict[str, NativeObject]]

    @property
    def is_self_contained(self) -> bool:
        return not self.prerequisites and self.filter_spec is None

    @property
    def requires_prerequisites(self) -> bool:
        return bool(self.prerequisites)

    @property
    def is_filtered(self) -> bool:
        return self.filter_spec is not None


def _parse_native_oid(raw: bytes, *, context: str) -> str:
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError(f"Non-ASCII {context} object id") from exc
    if not _HEX40_RE.fullmatch(text):
        raise ValueError(f"Malformed {context} object id; expected 40-hex SHA-1")
    return text.lower()


def _validate_bundle_refname(raw: bytes) -> str:
    try:
        name = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Invalid UTF-8 bundle reference name") from exc
    if not name or "\x00" in name:
        raise ValueError("Invalid empty/NUL bundle reference name")
    if name == "HEAD":
        return name
    try:
        check_ref_format(name)
    except ValueError as exc:
        raise ValueError(f"Invalid bundle reference name {name!r}: {exc}") from exc
    return name


def _parse_capability(line: bytes) -> Tuple[str, Optional[bytes]]:
    if not line.startswith(b"@"):
        raise ValueError("Malformed bundle capability record")
    payload = line[1:]
    key_raw, marker, value = payload.partition(b"=")
    try:
        key = key_raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("Non-ASCII bundle capability name") from exc
    if not _CAPABILITY_KEY_RE.fullmatch(key):
        raise ValueError(f"Invalid bundle capability name {key!r}")
    if marker:
        if b"\x00" in value or b"\n" in value:
            raise ValueError(f"Invalid value for bundle capability {key!r}")
        return key, value
    return key, None


def _validate_capabilities(
    version: int,
    capabilities: Dict[str, Optional[bytes]],
) -> Tuple[str, Optional[str]]:
    if version == 2 and capabilities:
        raise ValueError("Bundle v2 does not support capability records")

    unknown = sorted(set(capabilities) - {"object-format", "filter"})
    if unknown:
        raise RuntimeError(
            "Unsupported Git bundle capability: " + ", ".join(unknown)
        )

    object_format = "sha1"
    raw_format = capabilities.get("object-format")
    if "object-format" in capabilities:
        if raw_format is None:
            raise ValueError("Bundle object-format capability requires a value")
        try:
            object_format = raw_format.decode("ascii")
        except UnicodeDecodeError as exc:
            raise ValueError("Non-ASCII bundle object-format value") from exc
    if object_format != "sha1":
        raise RuntimeError(
            f"Unsupported bundle object format: {object_format}; expected sha1"
        )

    filter_spec: Optional[str] = None
    if "filter" in capabilities:
        raw_filter = capabilities["filter"]
        if raw_filter is None or not raw_filter:
            raise ValueError("Bundle filter capability requires a non-empty value")
        try:
            filter_spec = raw_filter.decode("ascii")
        except UnicodeDecodeError as exc:
            raise ValueError("Non-ASCII bundle filter capability") from exc
        if any(ch in filter_spec for ch in ("\x00", "\r", "\n")):
            raise ValueError("Invalid bundle filter capability")

    return object_format, filter_spec


def _read_pack_object_header(pack: bytes, offset: int, limit: int) -> Tuple[int, int, int]:
    if offset >= limit:
        raise ValueError("Truncated pack object header")
    byte = pack[offset]
    offset += 1
    packed_type = (byte >> 4) & 7
    size = byte & 0x0F
    shift = 4
    while byte & 0x80:
        if offset >= limit:
            raise ValueError("Truncated pack object size")
        byte = pack[offset]
        offset += 1
        size |= (byte & 0x7F) << shift
        shift += 7
        if shift > 67:
            raise ValueError("Pack object size is unreasonably large")
    return packed_type, size, offset


def _read_ofs_delta_base(pack: bytes, offset: int, limit: int, entry_offset: int) -> int:
    if offset >= limit:
        raise ValueError("Truncated OFS_DELTA base offset")
    byte = pack[offset]
    offset += 1
    distance = byte & 0x7F
    while byte & 0x80:
        if offset >= limit:
            raise ValueError("Truncated OFS_DELTA base offset")
        byte = pack[offset]
        offset += 1
        distance = ((distance + 1) << 7) | (byte & 0x7F)
    base_offset = entry_offset - distance
    if base_offset < 12 or base_offset >= entry_offset:
        raise ValueError("Invalid OFS_DELTA base offset")
    return offset


def _verify_pack_structure(pack: bytes) -> Tuple[int, int]:
    """Verify one SHA-1 pack envelope and every compressed entry boundary."""

    if len(pack) < 32 or not pack.startswith(b"PACK"):
        raise ValueError("Bundle payload does not contain a valid PACK header")
    version, count = struct.unpack(">II", pack[4:12])
    if version not in (2, 3):
        raise ValueError(f"Unsupported packfile version: {version}")

    trailer_offset = len(pack) - 20
    if hashlib.sha1(pack[:trailer_offset]).digest() != pack[trailer_offset:]:
        raise ValueError("Bundle packfile checksum mismatch")

    offset = 12
    for _ in range(count):
        entry_offset = offset
        packed_type, size, offset = _read_pack_object_header(
            pack, offset, trailer_offset
        )
        if packed_type == 6:
            offset = _read_ofs_delta_base(
                pack, offset, trailer_offset, entry_offset
            )
        elif packed_type == 7:
            if offset + 20 > trailer_offset:
                raise ValueError("Truncated REF_DELTA base object id")
            offset += 20
        elif packed_type not in (1, 2, 3, 4):
            raise ValueError(f"Unsupported pack object type: {packed_type}")

        compressed_available = trailer_offset - offset
        inflater = zlib.decompressobj()
        try:
            raw = inflater.decompress(pack[offset:trailer_offset])
        except zlib.error as exc:
            raise ValueError("Invalid zlib stream in bundle packfile") from exc
        if not inflater.eof:
            raise ValueError("Truncated zlib stream in bundle packfile")
        consumed = compressed_available - len(inflater.unused_data)
        if consumed <= 0:
            raise ValueError("Empty compressed object in bundle packfile")
        if len(raw) != size:
            raise ValueError(
                "Bundle pack object decompressed size does not match its header"
            )
        offset += consumed

    if offset != trailer_offset:
        raise ValueError("Trailing bytes between bundle pack entries and checksum")
    return version, count


def parse_git_bundle(data: bytes) -> GitBundlePayload:
    """Parse and verify one Git bundle v2/v3 payload without repository writes."""

    if data.startswith(b"# v2 git bundle\n"):
        version = 2
        offset = len(b"# v2 git bundle\n")
    elif data.startswith(b"# v3 git bundle\n"):
        version = 3
        offset = len(b"# v3 git bundle\n")
    else:
        raise ValueError("Unsupported or malformed Git bundle signature")

    capabilities: Dict[str, Optional[bytes]] = {}
    prerequisites = []
    refs: Dict[str, str] = {}
    prerequisite_oids = set()
    state = "capabilities"

    while True:
        newline = data.find(b"\n", offset)
        if newline < 0:
            raise ValueError("Git bundle header is missing its blank-line terminator")
        line = data[offset:newline]
        offset = newline + 1
        if line == b"":
            break
        if b"\x00" in line or b"\r" in line:
            raise ValueError("Invalid control byte in Git bundle header")

        if line.startswith(b"@"):
            if version != 3 or state != "capabilities":
                raise ValueError("Bundle capability appears outside the v3 capability section")
            key, value = _parse_capability(line)
            if key in capabilities:
                raise ValueError(f"Duplicate Git bundle capability {key!r}")
            capabilities[key] = value
            continue

        if line.startswith(b"-"):
            if state == "references":
                raise ValueError("Bundle prerequisite appears after a reference")
            state = "prerequisites"
            body = line[1:]
            oid_raw, separator, comment = body.partition(b" ")
            if not separator:
                raise ValueError("Malformed Git bundle prerequisite")
            oid = _parse_native_oid(oid_raw, context="bundle prerequisite")
            if oid in prerequisite_oids:
                raise ValueError(f"Duplicate Git bundle prerequisite {oid}")
            prerequisite_oids.add(oid)
            prerequisites.append(BundlePrerequisite(oid=oid, comment=comment))
            continue

        state = "references"
        oid_raw, separator, ref_raw = line.partition(b" ")
        if not separator or b" " in ref_raw:
            raise ValueError("Malformed Git bundle reference")
        oid = _parse_native_oid(oid_raw, context="bundle reference")
        refname = _validate_bundle_refname(ref_raw)
        if refname in refs:
            raise ValueError(f"Duplicate Git bundle reference {refname!r}")
        refs[refname] = oid

    if not refs:
        raise ValueError("Git bundle does not advertise any references")

    object_format, filter_spec = _validate_capabilities(version, capabilities)
    pack = data[offset:]
    pack_version, pack_entries = _verify_pack_structure(pack)

    objects: Optional[Dict[str, NativeObject]] = None
    if not prerequisites:
        objects = PackParser(pack).parse()
        missing_tips = sorted(set(refs.values()) - set(objects))
        if missing_tips:
            raise ValueError(
                "Self-contained Git bundle references objects absent from its pack"
            )

    return GitBundlePayload(
        version=version,
        object_format=object_format,
        capabilities=capabilities,
        prerequisites=tuple(prerequisites),
        refs=refs,
        filter_spec=filter_spec,
        pack=pack,
        pack_version=pack_version,
        pack_entries=pack_entries,
        objects=objects,
    )
