"""High-level Smart HTTP repository fetch for protocol-v2 packfile URIs.

Phase327 binds Phase318's protocol-v2 transport result to the exact-green
Phase326 repository transaction.  The adapter deliberately stays thin: wire
negotiation remains owned by :mod:`protocol_v2_packfile_uris`, while external
pack verification, SHA-256 import, certification, locking, and ref CAS remain
owned by :mod:`protocol_v2_packfile_uri_transaction`.

The additional trust boundary in this module is *request binding*.  Every root
that the caller asks the repository transaction to certify must be one of the
remote-native SHA-1 tips that this exact transport result advertised and would
therefore have included in its terminating fetch ``want`` set.  This prevents a
caller from combining an otherwise-valid downloaded object graph with an
unrelated publication plan.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Optional, Sequence

from .protocol_v2_packfile_uri_refs import PackfileUriRefPublication
from .protocol_v2_packfile_uri_transaction import (
    PackfileUriFetchTransactionResult,
    execute_packfile_uri_fetch_transaction,
)
from .protocol_v2_packfile_uris import (
    SmartHttpV2PackfileUriClient,
    V2PackfileUriFetchResult,
    normalize_packfile_uri_protocols,
)
from .remote import Advertisement
from .repo import Repository

_HEX = frozenset("0123456789abcdef")


@dataclass(frozen=True)
class SmartHttpV2PackfileUriRepositoryResult:
    """Successful wire + repository result for one packfile-URI fetch."""

    transport: V2PackfileUriFetchResult
    transaction: PackfileUriFetchTransactionResult


def _native_oid(value: object, *, field: str) -> str:
    if not isinstance(value, str) or len(value) != 40:
        raise ValueError(f"{field} must be a full remote-native SHA-1 object id")
    lowered = value.lower()
    if any(ch not in _HEX for ch in lowered):
        raise ValueError(f"{field} must be a full remote-native SHA-1 object id")
    return lowered


def _advertised_fetch_roots(advertisement: Advertisement) -> frozenset[str]:
    """Return exactly the native tips Phase318 includes in its ``want`` set."""

    if not isinstance(advertisement, Advertisement):
        raise TypeError("packfile-URI transport result requires an Advertisement")

    roots = set()
    for refname, oid in advertisement.refs.items():
        if (
            refname == "HEAD"
            or refname.startswith("refs/heads/")
            or (refname.startswith("refs/tags/") and not refname.endswith("^{}"))
        ):
            roots.add(_native_oid(oid, field=f"advertised ref {refname!r}"))
    return frozenset(roots)


def _validate_transport_publication_binding(
    result: V2PackfileUriFetchResult,
    expected_roots: Mapping[str, bytes | str],
    publications: Mapping[str, PackfileUriRefPublication],
) -> None:
    """Bind repository roots to native tips requested by this transport result."""

    if not isinstance(result, V2PackfileUriFetchResult):
        raise TypeError("packfile-URI repository fetch requires a Phase318 transport result")
    if not isinstance(expected_roots, Mapping):
        raise TypeError("packfile-URI expected roots must be a mapping")
    if not expected_roots:
        raise ValueError("packfile-URI repository fetch requires at least one expected root")
    if not isinstance(publications, Mapping):
        raise TypeError("packfile-URI ref publications must be a mapping")
    if not publications:
        raise ValueError("packfile-URI repository fetch requires at least one ref publication")

    advertised = _advertised_fetch_roots(result.advertisement)
    if not advertised:
        raise RuntimeError("packfile-URI transport result advertised no fetchable native roots")

    normalized_expected: dict[str, str] = {}
    for native_oid in expected_roots:
        normalized = _native_oid(native_oid, field="expected root")
        if normalized in normalized_expected:
            raise ValueError(
                "packfile-URI expected roots contain duplicate native identities"
            )
        normalized_expected[normalized] = native_oid
        if normalized not in advertised:
            raise ValueError(
                "packfile-URI expected root was not advertised by this transport fetch"
            )

    for refname, publication in publications.items():
        if not isinstance(publication, PackfileUriRefPublication):
            raise TypeError(
                "packfile-URI publication values must be PackfileUriRefPublication"
            )
        native_oid = _native_oid(publication.native_oid, field="publication native root")
        if native_oid not in normalized_expected:
            raise ValueError(
                f"packfile-URI publication {refname!r} is not declared in expected_roots"
            )
        if native_oid not in advertised:
            raise ValueError(
                f"packfile-URI publication {refname!r} was not advertised by this transport fetch"
            )


def fetch_packfile_uris_into_repository(
    repo: Repository,
    client: SmartHttpV2PackfileUriClient,
    protocols: Sequence[str],
    expected_roots: Mapping[str, bytes | str],
    publications: Mapping[str, PackfileUriRefPublication],
    *,
    haves: Optional[Iterable[str]] = None,
    advertisement: Optional[Advertisement] = None,
    shallow: Iterable[str] = (),
    deepen: Optional[int] = None,
    deepen_relative: bool = False,
    message: str = "fetch: publish verified protocol-v2 packfile-uri transaction",
    external_timeout: Optional[int] = None,
    max_pack_bytes: int = 256 * 1024 * 1024,
    max_total_bytes: int = 512 * 1024 * 1024,
    max_packs: int = 64,
    opener=None,
) -> Optional[SmartHttpV2PackfileUriRepositoryResult]:
    """Fetch through protocol v2 and atomically publish certified local refs.

    ``None`` preserves the existing explicit fallback signal when discovery
    proves that the server did not speak protocol v2.  Otherwise the complete
    Phase318 transport result is first bound to the caller's expected native
    roots, then handed to Phase326 without re-parsing, re-hashing, or translating
    identities.

    Remote identities remain full 40-hex SHA-1 values.  The transaction creates
    local 64-hex SHA-256 identities only by importing actual Git object content.
    No transport OID or pack checksum is padded, truncated, or treated as a local
    object identity.
    """

    if not isinstance(repo, Repository):
        raise TypeError("packfile-URI repository fetch requires a Repository")
    if not isinstance(client, SmartHttpV2PackfileUriClient):
        raise TypeError(
            "packfile-URI repository fetch requires a SmartHttpV2PackfileUriClient"
        )

    requested_protocols = normalize_packfile_uri_protocols(protocols)
    result = client.fetch_with_packfile_uris(
        requested_protocols,
        haves=haves,
        advertisement=advertisement,
        shallow=shallow,
        deepen=deepen,
        deepen_relative=deepen_relative,
    )
    if result is None:
        return None
    if not isinstance(result, V2PackfileUriFetchResult):
        raise TypeError("packfile-URI client returned an unexpected fetch result type")

    _validate_transport_publication_binding(result, expected_roots, publications)

    timeout = client.timeout if external_timeout is None else external_timeout
    transaction = execute_packfile_uri_fetch_transaction(
        repo,
        result.packfile_uris,
        result.objects,
        expected_roots,
        publications,
        message=message,
        timeout=timeout,
        max_pack_bytes=max_pack_bytes,
        max_total_bytes=max_total_bytes,
        max_packs=max_packs,
        opener=opener,
    )
    return SmartHttpV2PackfileUriRepositoryResult(result, transaction)
