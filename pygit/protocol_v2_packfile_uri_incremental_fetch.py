"""Repository-safe incremental protocol-v2 packfile-URI fetch integration.

Phase334 binds Phase333's read-only negotiation plan to both sides of the fetch
boundary at once: native SHA-1 ``have`` tips go to the protocol-v2 request, while
the exact same plan's validated native->local closure goes to the staging
``NativeImporter`` as known existing objects.

Keeping those two values inseparable is the core safety property.  A server may
legitimately omit any object reachable from a ``have``; advertising a have
without giving the importer the corresponding local identities would turn a
valid incremental response into an incomplete graph.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Optional, Sequence

from .protocol_v2_packfile_uri_batch import (
    DownloadedPackfileUriBatch,
    download_packfile_uris,
)
from .protocol_v2_packfile_uri_connectivity import certify_packfile_uri_roots
from .protocol_v2_packfile_uri_incremental import (
    PackfileUriIncrementalState,
    plan_packfile_uri_incremental_state,
)
from .protocol_v2_packfile_uri_remote_fetch import _configured_remote_url
from .protocol_v2_packfile_uri_repository import _validate_transport_publication_binding
from .protocol_v2_packfile_uri_stage import stage_packfile_uri_import
from .protocol_v2_packfile_uri_tracking import (
    PackfileUriRemoteTrackingPlan,
    plan_packfile_uri_remote_tracking_publication,
)
from .protocol_v2_packfile_uri_transaction import (
    PackfileUriFetchTransactionResult,
    _acquire_publication_guard_locks,
    _assert_publication_state_unchanged,
    _preflight_publication_plan,
    _release_publication_guard_locks,
    _snapshot_publication_state,
)
from .protocol_v2_packfile_uri_refs import (
    PackfileUriRefPublication,
    publish_packfile_uri_refs,
)
from .protocol_v2_packfile_uris import (
    SmartHttpV2PackfileUriClient,
    V2PackfileUriFetchResult,
    normalize_packfile_uri_protocols,
)
from .remote import Advertisement, NativeObject
from .repo import Repository


@dataclass(frozen=True)
class IncrementalNamedRemotePackfileUriFetchResult:
    """Successful mapped incremental named-remote fetch and ref publication."""

    remote: str
    url: str
    advertisement: Advertisement
    plan: PackfileUriRemoteTrackingPlan
    incremental: PackfileUriIncrementalState
    transport: V2PackfileUriFetchResult
    transaction: PackfileUriFetchTransactionResult


def _download_optional_packfile_uris(
    descriptors,
    *,
    timeout: int,
    max_pack_bytes: int,
    max_total_bytes: int,
    max_packs: int,
    opener,
) -> DownloadedPackfileUriBatch:
    """Download an external batch, or represent a valid inline-only response.

    Advertising/requesting ``packfile-uris`` does not require the server to
    offload any pack.  A normal inline ``packfile`` section with zero URI
    descriptors is therefore a valid protocol-v2 result.

    The existing batch downloader remains authoritative for iterable/type and
    resource-bound validation.  Phase334 translates only its exact empty-batch
    rejection into an explicit verified empty batch; every other error is
    preserved unchanged.
    """

    try:
        items = tuple(descriptors)
    except TypeError as exc:
        raise TypeError("protocol-v2 packfile URI descriptors must be iterable") from exc

    try:
        return download_packfile_uris(
            items,
            timeout=timeout,
            max_pack_bytes=max_pack_bytes,
            max_total_bytes=max_total_bytes,
            max_packs=max_packs,
            opener=opener,
        )
    except ValueError as exc:
        if items or str(exc) != "protocol-v2 packfile URI batch must contain at least one descriptor":
            raise
        return DownloadedPackfileUriBatch((), {}, 0)


def execute_incremental_packfile_uri_fetch_transaction(
    repo: Repository,
    descriptors,
    inline_objects: Mapping[str, NativeObject],
    expected_roots: Mapping[str, bytes | str],
    publications: Mapping[str, PackfileUriRefPublication],
    incremental: PackfileUriIncrementalState,
    *,
    message: str = "fetch: publish verified incremental packfile-uri transaction",
    timeout: int = 30,
    max_pack_bytes: int = 256 * 1024 * 1024,
    max_total_bytes: int = 512 * 1024 * 1024,
    max_packs: int = 64,
    opener=None,
) -> PackfileUriFetchTransactionResult:
    """Run the existing guarded repository transaction with mapped known objects.

    This deliberately mirrors Phase324-326's ordering and reuses its exact
    preflight/snapshot/lock helpers.  The only semantic differences are that
    Phase333's validated ``known_native_to_local`` closure is supplied to the
    known-aware staging importer and a server may keep the complete pack inline
    instead of returning any external URI descriptor.
    """

    if not isinstance(repo, Repository):
        raise TypeError("incremental packfile-URI fetch transaction requires a Repository")
    if not isinstance(inline_objects, Mapping):
        raise TypeError("incremental packfile-URI inline objects must be a mapping")
    if not isinstance(incremental, PackfileUriIncrementalState):
        raise TypeError(
            "incremental packfile-URI transaction requires PackfileUriIncrementalState"
        )
    if not isinstance(message, str) or not message.strip():
        raise ValueError("incremental packfile-URI transaction message must be non-empty")

    _preflight_publication_plan(expected_roots, publications)
    mutable_state = _snapshot_publication_state(repo, publications)

    batch = _download_optional_packfile_uris(
        descriptors,
        timeout=timeout,
        max_pack_bytes=max_pack_bytes,
        max_total_bytes=max_total_bytes,
        max_packs=max_packs,
        opener=opener,
    )
    staged = stage_packfile_uri_import(
        repo.store,
        inline_objects,
        batch,
        known_native_to_local=incremental.known_native_to_local,
    )
    certificate = certify_packfile_uri_roots(repo.store, staged, expected_roots)

    guard_locks = _acquire_publication_guard_locks(repo)
    try:
        _assert_publication_state_unchanged(repo, publications, mutable_state)
        published_refs = publish_packfile_uri_refs(
            repo,
            certificate,
            publications,
            message=message,
        )
    finally:
        _release_publication_guard_locks(guard_locks)

    return PackfileUriFetchTransactionResult(
        batch=batch,
        staged=staged,
        certificate=certificate,
        published_refs=dict(published_refs),
    )


def fetch_named_remote_incrementally_with_packfile_uris(
    repo: Repository,
    remote: str = "origin",
    *,
    protocols: Sequence[str] = ("https",),
    branches: Optional[Iterable[str]] = None,
    shallow: Iterable[str] = (),
    deepen: Optional[int] = None,
    deepen_relative: bool = False,
    timeout: int = 30,
    server_options: Sequence[str] = (),
    message: Optional[str] = None,
    external_timeout: Optional[int] = None,
    max_pack_bytes: int = 256 * 1024 * 1024,
    max_total_bytes: int = 512 * 1024 * 1024,
    max_packs: int = 64,
    opener=None,
) -> Optional[IncrementalNamedRemotePackfileUriFetchResult]:
    """Fetch a configured remote with automatically mapped incremental haves.

    The function discovers refs before repository mutation, builds the ordinary
    Phase328 tracking/ref CAS plan, and then asks Phase333 whether each existing
    tracking tip has a complete validated LMAP-backed local closure.

    The exact resulting ``haves`` are sent to Phase318's protocol-v2 request and
    the paired ``known_native_to_local`` mapping is kept attached to the same
    transaction.  Missing map coverage simply yields no have for that ref and
    therefore preserves full-fetch behavior.  The server may either offload packs
    through URI descriptors or keep the terminating pack entirely inline.  No
    SHA-1 identity is synthesized in either path.

    ``None`` is returned only when initial discovery proves that the remote is not
    speaking protocol v2.  A downgrade after successful v2 discovery fails closed.
    """

    if not isinstance(repo, Repository):
        raise TypeError("incremental packfile-URI named fetch requires a Repository")

    url = _configured_remote_url(repo, remote)
    requested_protocols = normalize_packfile_uri_protocols(protocols)
    client = SmartHttpV2PackfileUriClient(
        url,
        timeout=timeout,
        server_options=server_options,
    )

    advertisement = client.discover_refs()
    if advertisement is None:
        return None
    if not isinstance(advertisement, Advertisement):
        raise TypeError("incremental packfile-URI ref discovery returned an unexpected type")

    plan = plan_packfile_uri_remote_tracking_publication(
        repo,
        advertisement,
        remote=remote,
        branches=branches,
    )
    incremental = plan_packfile_uri_incremental_state(repo, plan)

    transport = client.fetch_with_packfile_uris(
        requested_protocols,
        haves=incremental.haves,
        advertisement=advertisement,
        shallow=shallow,
        deepen=deepen,
        deepen_relative=deepen_relative,
    )
    if transport is None:
        raise RuntimeError(
            "Remote stopped speaking protocol v2 during incremental packfile-URI fetch"
        )
    if not isinstance(transport, V2PackfileUriFetchResult):
        raise TypeError("incremental packfile-URI client returned an unexpected fetch type")

    _validate_transport_publication_binding(
        transport,
        plan.expected_roots,
        plan.publications,
    )

    publication_message = message or (
        f"fetch: {remote} via verified incremental protocol-v2 packfile-uri"
    )
    transaction = execute_incremental_packfile_uri_fetch_transaction(
        repo,
        transport.packfile_uris,
        transport.objects,
        plan.expected_roots,
        plan.publications,
        incremental,
        message=publication_message,
        timeout=client.timeout if external_timeout is None else external_timeout,
        max_pack_bytes=max_pack_bytes,
        max_total_bytes=max_total_bytes,
        max_packs=max_packs,
        opener=opener,
    )

    return IncrementalNamedRemotePackfileUriFetchResult(
        remote=remote,
        url=url,
        advertisement=advertisement,
        plan=plan,
        incremental=incremental,
        transport=transport,
        transaction=transaction,
    )
