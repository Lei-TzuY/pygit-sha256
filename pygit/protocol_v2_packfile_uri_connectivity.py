"""Certify requested roots after packfile-URI staging and before ref publication.

Phase322 adds a narrow pre-ref-update trust boundary on top of Phase321.  The
complete native object graph has already crossed the content-derived importer
boundary; this module proves that every requested remote-native tip maps to a
published local SHA-256 object of the caller-expected Git object type.

Phase338 additionally permits an expected root to be satisfied by an explicit
validated incremental native->local known mapping when a fully up-to-date fetch
contains no newly staged object for that root.

No refs, HEAD, reflogs, or promisor metadata are mutated here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping, Optional

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


def _validate_staged_local_oid(oid: object) -> str:
    """Preserve Phase322's established staged-root validation contract."""

    if not isinstance(oid, str) or len(oid) != 64:
        raise ValueError("protocol-v2 staged local root id must be a full SHA-256")
    try:
        bytes.fromhex(oid)
    except ValueError as exc:
        raise ValueError("protocol-v2 staged local root id must be hexadecimal") from exc
    return oid


def _validate_known_local_oid(oid: object) -> str:
    """Validate a Phase333/334 known identity without normalizing it."""

    if not isinstance(oid, str) or len(oid) != 64 or oid != oid.lower():
        raise ValueError(
            "protocol-v2 known local root id must be a full lowercase SHA-256"
        )
    try:
        bytes.fromhex(oid)
    except ValueError as exc:
        raise ValueError("protocol-v2 known local root id must be hexadecimal") from exc
    return oid


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
    *,
    known_native_to_local: Optional[Mapping[str, str]] = None,
) -> PackfileUriRootCertificate:
    """Validate requested roots without publishing refs.

    ``expected_roots`` maps genuine remote-native SHA-1 object ids to the Git
    object type required by the caller's ref semantics. For example, a branch
    publication should require ``commit`` while an annotated tag publication can
    require ``tag``.

    A staged mapping is authoritative when present and must still appear in the
    staged import's newly published local-OID set. Phase338 permits a missing
    staged root to fall back to ``known_native_to_local``. This is intended for a
    fully up-to-date incremental response where the server sent no new object for
    a tip already proven by Phase333's complete LMAP-backed local closure.

    Known fallback is explicit rather than implicit. The local SHA-256 is
    validated syntactically, re-read from the destination store, re-hashed, and
    checked against the caller-required Git object type before certification.
    A staged root that is also present in the known map must agree exactly.

    This is intentionally read-only. A later transaction can use the returned
    certificate as input to compare-and-swap ref publication.
    """

    if not isinstance(store, ObjectStore):
        raise TypeError("protocol-v2 root certification requires an ObjectStore")
    if not isinstance(staged, StagedPackfileUriImport):
        raise TypeError("protocol-v2 root certification requires a staged import")
    if not isinstance(expected_roots, Mapping):
        raise TypeError("protocol-v2 expected roots must be a mapping")
    if not expected_roots:
        raise ValueError("protocol-v2 root certification requires at least one root")
    if known_native_to_local is None:
        known_native_to_local = {}
    elif not isinstance(known_native_to_local, Mapping):
        raise TypeError("protocol-v2 known root identities must be a mapping")

    published = set(staged.local_oids)
    certified: Dict[str, str] = {}
    normalized_types: Dict[str, bytes] = {}

    for native_oid, expected_type in expected_roots.items():
        _validate_native_oid(native_oid)
        normalized = _normalize_expected_type(expected_type)

        staged_local = staged.native_to_local.get(native_oid)
        known_local = known_native_to_local.get(native_oid)

        if staged_local is not None:
            local_oid = _validate_staged_local_oid(staged_local)
            if local_oid not in published:
                raise ValueError("protocol-v2 requested root was not published by the staged import")
            if known_local is not None:
                known_oid = _validate_known_local_oid(known_local)
                if known_oid != local_oid:
                    raise ValueError(
                        "protocol-v2 staged root contradicts its known native-to-local mapping"
                    )
        else:
            if known_local is None:
                raise ValueError(
                    "protocol-v2 requested root was not present in the staged import or known objects"
                )
            local_oid = _validate_known_local_oid(known_local)

        obj = store.read(local_oid)
        if obj.hash() != local_oid:
            raise RuntimeError("protocol-v2 certified root changed local SHA-256 identity")
        if obj.type_name != normalized:
            actual = obj.type_name.decode("ascii", errors="replace")
            wanted = normalized.decode("ascii")
            raise ValueError(
                f"protocol-v2 requested root type mismatch: expected {wanted}, got {actual}"
            )

        certified[native_oid] = local_oid
        normalized_types[native_oid] = normalized

    return PackfileUriRootCertificate(certified, normalized_types)
