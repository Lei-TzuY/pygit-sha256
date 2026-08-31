"""Repository-safe incremental protocol-v2 packfile-URI fetch integration.

Phase334 binds Phase333's read-only negotiation plan to both sides of the fetch
boundary at once: native SHA-1 ``have`` tips go to the protocol-v2 request, while
the exact same plan's validated native->local closure goes to the staging
``NativeImporter`` as known existing objects.

Phase336 additionally persists every newly staged native->local identity set as
Git-compatible LMAP v1 compatibility metadata before any mutable ref publication.
This makes repeated incremental fetches self-feeding without synthesizing remote
SHA-1 identity from local SHA-256 object ids.

Phase338 completes the fully up-to-date path: a response with no new objects may
certify roots directly from the already-validated known mapping. No empty LMAP
file is published and known objects remain existing evidence rather than being
misreported as newly staged content.

Phase340 publishes Git-compatible ``FETCH_HEAD`` metadata for the named-remote
path. The file is truncated once protocol-v2 discovery succeeds, then populated
from certified local SHA-256 roots immediately before tracking-ref publication.
This mirrors native Git's distinction between fetched tips and successful local
tracking-ref updates: a later ref lock/CAS failure may still leave the verified
fetched tip in ``FETCH_HEAD``.

Phase343 upgrades the Phase336 LMAP step to Phase342's durable publication
boundary. A transaction that stages new objects cannot proceed to root
certification, FETCH_HEAD, or ref publication until the immutable compatibility
map has passed its directory durability fences.

Phase344 makes the two replace-style FETCH_HEAD writes crash-safe: each complete
file is fsynced to a same-directory temporary, atomically replaced, and followed
by a directory durability fence before the fetch may proceed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Mapping, Optional, Sequence

from .fetch_head_durable import write_fetch_head_durable as write_fetch_head
from .loose_object_map import PublishedLooseObjectMap
from .loose_object_map_durable import (
    publish_staged_loose_object_map_durable as publish_staged_loose_object_map,
)
from .protocol_v2_packfile_uri_batch import (
    DownloadedPackfileUriBatch,
    download_packfile_uris,
)
from .protocol_v2_packfile_uri_connectivity import (
    PackfileUriRootCertificate,
    certify_packfile_uri_roots,
)
from .protocol_v2_packfile_uri_incremental import (
    PackfileUriIncrementalState,
    plan_packfile_uri_incremental_state,
)
from .protocol_v2_packfile_uri_remote_fetch import _configured_remote_url
from .protocol_v2_packfile_uri_repository import _validate_transport_publication_binding
from .protocol_v2_packfile_uri_stage import StagedPackfileUriImport, stage_packfile_uri_import
from .protocol_v2_packfile_uri_tracking import (
    PackfileUriRemoteTrackingPlan,
    plan_packfile_uri_remote_tracking_publication,
)
from .protocol_v2_packfile_uri_transaction import (
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
class IncrementalPackfileUriFetchTransactionResult:
    """Successful incremental transaction with optional new LMAP publication."""

    batch: DownloadedPackfileUriBatch
    staged: StagedPackfileUriImport
    object_map: Optional[PublishedLooseObjectMap]
    certificate: PackfileUriRootCertificate
    published_refs: dict[str, str]


@dataclass(frozen=True)
class IncrementalNamedRemotePackfileUriFetchResult:
    """Successful mapped incremental named-remote fetch and ref publication."""

    remote: str
    url: str
    advertisement: Advertisement
    plan: PackfileUriRemoteTrackingPlan
    incremental: PackfileUriIncrementalState
    transport: V2PackfileUriFetchResult
    transaction: IncrementalPackfileUriFetchTransactionResult


def _download_optional_packfile_uris(
    descriptors,
    *,
    timeout: int,
    max_pack_bytes: int,
    max_total_bytes: int,
    max_packs: int,
    opener,
) -> DownloadedPackfileUriBatch:
    """Download an external batch, or represent a valid inline-only response."""

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


def _fetch_head_refs(
    remote: str,
    plan: PackfileUriRemoteTrackingPlan,
    certificate: PackfileUriRootCertificate,
) -> dict[str, str]:
    """Project certified tracking publications back to their source branch refs."""

    prefix = f"refs/remotes/{remote}/"
    result: dict[str, str] = {}
    for local_ref, publication in plan.publications.items():
        if not local_ref.startswith(prefix) or len(local_ref) == len(prefix):
            raise RuntimeError(
                "incremental packfile-URI FETCH_HEAD publication received a foreign tracking ref"
            )
        local_oid = certificate.native_to_local.get(publication.native_oid)
        if local_oid is None:
            raise RuntimeError(
                "incremental packfile-URI FETCH_HEAD publication root was not certified"
            )
        source_ref = f"refs/heads/{local_ref[len(prefix):]}"
        result[source_ref] = local_oid
    return result


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
    before_ref_publication: Optional[Callable[[PackfileUriRootCertificate], None]] = None,
) -> IncrementalPackfileUriFetchTransactionResult:
    """Run the guarded mapped-incremental repository transaction.

    Ordering is intentionally strict:

    ``download -> stage -> [new durable immutable LMAP] -> certify -> [fetch metadata] -> guard/CAS refs``.

    Newly staged native/local identities are durably published as one
    content-addressed immutable LMAP before root certification. A durability
    failure therefore aborts before FETCH_HEAD or mutable refs can advance. A
    fully up-to-date response may contain no new objects; in that case no new
    LMAP generation or durability fence is needed.
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
    if before_ref_publication is not None and not callable(before_ref_publication):
        raise TypeError("incremental packfile-URI pre-ref publication hook must be callable")

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
    object_map = (
        publish_staged_loose_object_map(repo, staged)
        if staged.native_to_local
        else None
    )
    certificate = certify_packfile_uri_roots(
        repo.store,
        staged,
        expected_roots,
        known_native_to_local=incremental.known_native_to_local,
    )

    if before_ref_publication is not None:
        before_ref_publication(certificate)

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

    return IncrementalPackfileUriFetchTransactionResult(
        batch=batch,
        staged=staged,
        object_map=object_map,
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
    """Fetch a configured remote with automatically mapped incremental haves."""

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

    write_fetch_head(repo.pygit_dir, {}, source=url)

    explicit_branches = branches is not None
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

    def publish_certified_fetch_head(certificate: PackfileUriRootCertificate) -> None:
        fetched = _fetch_head_refs(remote, plan, certificate)
        write_fetch_head(
            repo.pygit_dir,
            fetched,
            source=url,
            mergeable=tuple(fetched) if explicit_branches else (),
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
        before_ref_publication=publish_certified_fetch_head,
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
