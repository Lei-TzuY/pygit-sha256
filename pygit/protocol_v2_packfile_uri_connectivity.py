"""Certify requested roots after packfile-URI staging and before ref publication.

Phase322 adds a narrow pre-ref-update trust boundary on top of Phase321.  The
complete native object graph has already crossed the content-derived importer
boundary; this module proves that every requested remote-native tip maps to a
published local SHA-256 object of the caller-expected Git object type.

No refs, HEAD, reflogs, or promisor metadata are mutated here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping

from .protocol_v2_packfile_uri_stage import StagedPackfileUriImport
from .store import ObjectStore

_ALLOWED_ROOT_TYPES = frozenset({b"blob", b"tree", b"commit", b"tag"})


@dataclass(frozen=True)
class PackfileUriRootCertificate:
    """Validated native-root to local-SHA-256 mapping ready for ref orchestration."""

    native_to_local: Dict[str, str]
    expected_types: Dict[str, bytes]


def _validate_native_oid(oid: str) -> None:
    if not isinstance(oid, str) or len(oid) != 40:
        raise ValueError("protocol-v2 root id must be a full remote-native SHA-1")
    try:
        bytes.fromhex(oid)
    except ValueError as exc:
        raise ValueError("protocol-v2 root id must be hexadecimal") from exc


def _normalize_expected_type(value: bytes | str) -> bytes:
    if isinstance(value, str):
        try:
            value = value.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ValueError("protocol-v2 expected root type must be ASCII") from exc
    if not isinstance(value, bytes) or value not in _ALLOWED_ROOT_TYPES:
        raise ValueError("protocol-v2 expected root type must be blob/tree/commit/tag")
    return value


def certify_packfile_uri_roots(
    store: ObjectStore,
    staged: StagedPackfileUriImport,
    expected_roots: Mapping[str, bytes | str],
) -> PackfileUriRootCertificate:
    """Validate requested roots without publishing refs.

    ``expected_roots`` maps genuine remote-native SHA-1 object ids to the Git
    object type required by the caller's ref semantics.  For example, a branch
    publication should require ``commit`` while an annotated tag publication can
    require ``tag``.

    Certification succeeds only when every requested native root was part of the
    exact Phase321 staged import, its mapped local SHA-256 id was included in that
    import's published object set, the local object is readable, and its decoded
    object type matches the caller's expectation.

    This is intentionally read-only.  A later transaction can use the returned
    certificate as input to compare-and-swap ref publication.  Failure here can
    therefore leave only unreachable immutable objects from Phase321, never a ref
    pointing at an unverified or type-confused target.
    """

    if not isinstance(store, ObjectStore):
        raise TypeError("protocol-v2 root certification requires an ObjectStore")
    if not isinstance(staged, StagedPackfileUriImport):
        raise TypeError("protocol-v2 root certification requires a staged import")
    if not isinstance(expected_roots, Mapping):
        raise TypeError("protocol-v2 expected roots must be a mapping")
    if not expected_roots:
        raise ValueError("protocol-v2 root certification requires at least one root")

    published = set(staged.local_oids)
    certified: Dict[str, str] = {}
    normalized_types: Dict[str, bytes] = {}

    for native_oid, expected_type in expected_roots.items():
        _validate_native_oid(native_oid)
        normalized = _normalize_expected_type(expected_type)

        local_oid = staged.native_to_local.get(native_oid)
        if local_oid is None:
            raise ValueError("protocol-v2 requested root was not present in the staged import")
        if not isinstance(local_oid, str) or len(local_oid) != 64:
            raise ValueError("protocol-v2 staged local root id must be a full SHA-256")
        try:
            bytes.fromhex(local_oid)
        except ValueError as exc:
            raise ValueError("protocol-v2 staged local root id must be hexadecimal") from exc
        if local_oid not in published:
            raise ValueError("protocol-v2 requested root was not published by the staged import")

        obj = store.read(local_oid)
        if obj.hash() != local_oid:
            raise RuntimeError("protocol-v2 published root changed local SHA-256 identity")
        if obj.type_name != normalized:
            actual = obj.type_name.decode("ascii", errors="replace")
            wanted = normalized.decode("ascii")
            raise ValueError(
                f"protocol-v2 requested root type mismatch: expected {wanted}, got {actual}"
            )

        certified[native_oid] = local_oid
        normalized_types[native_oid] = normalized

    return PackfileUriRootCertificate(certified, normalized_types)
