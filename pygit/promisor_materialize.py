"""Lazy materialization for objects promised by partial-fetch remotes.

Phase212 records omitted native Git objects without inventing local SHA-256
identities. Phase213 added single-object read-time materialization. Phase214
extends that primitive with a batched form so initial partial-clone checkout can
resolve all blobs needed by the selected worktree in one protocol-v2 request.

Phase221 removes the historical single-promisor restriction. Missing objects are
now attempted against configured promisor remotes in deterministic repository
configuration order, shrinking the unresolved set after every successful remote.
This matches Git's multi-promisor fallback model while preserving batched wants
whenever one remote can satisfy several requested objects.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, Sequence, Tuple

from .fetch_importer import TagPreservingNativeImporter
from .fetch_server_option_config import configured_server_options
from .promisor import (
    PromisorMissingError,
    promised_kind,
    read_promisor_state,
    resolved_native_objects,
    update_promisor_state,
)
from .protocol_v2_fetch import SmartHttpV2FetchClient, build_fetch_request
from .remote import NativeObject, PackParser


def _validate_native_oid(native_oid: str) -> str:
    value = native_oid.lower()
    if len(value) != 40:
        raise ValueError("promisor object id must be a 40-hex SHA-1")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError("promisor object id must be a 40-hex SHA-1") from exc
    return value


def _promisor_remote(pygit_dir: Path, native_oid: str) -> str:
    """Return the only promisor remote for the legacy single-remote seam."""
    state = read_promisor_state(pygit_dir)
    if native_oid not in state["promised"]:
        raise PromisorMissingError(native_oid)
    remotes = list(state["remotes"])
    if len(remotes) != 1:
        raise RuntimeError(
            "cannot materialize promisor object: repository does not identify exactly one promisor remote"
        )
    return remotes[0]


def _promisor_remotes_for_many(pygit_dir: Path, native_oids: Sequence[str]) -> Tuple[str, ...]:
    """Validate promises and return every recorded promisor remote.

    The persistent promise set is intentionally global rather than assigning a
    hard owner to each object. This mirrors Git's assumption that configured
    promisor remotes may be tried one after another for a missing object.
    """
    if not native_oids:
        raise ValueError("promisor materialization requires at least one object id")
    state = read_promisor_state(pygit_dir)
    for native_oid in native_oids:
        if native_oid not in state["promised"]:
            raise PromisorMissingError(native_oid)
    remotes = tuple(state["remotes"])
    if not remotes:
        raise RuntimeError(
            "cannot materialize promisor objects: repository does not identify a promisor remote"
        )
    return remotes


def _promisor_remote_for_many(pygit_dir: Path, native_oids: Sequence[str]) -> str:
    """Compatibility helper retaining Phase214's single-remote contract."""
    remotes = _promisor_remotes_for_many(pygit_dir, native_oids)
    if len(remotes) != 1:
        raise RuntimeError(
            "cannot materialize promisor objects: repository does not identify exactly one promisor remote"
        )
    return remotes[0]


def _fetch_native_objects(
    url: str,
    native_oids: Sequence[str],
    *,
    server_options: Sequence[str] = (),
) -> Dict[str, NativeObject]:
    """Fetch promised native objects without reapplying the repository filter."""
    wanted = tuple(dict.fromkeys(_validate_native_oid(oid) for oid in native_oids))
    if not wanted:
        raise ValueError("promisor materialization requires at least one object id")

    client = SmartHttpV2FetchClient(url, server_options=server_options)
    capabilities = client.discover_capabilities()
    if capabilities is None:
        raise RuntimeError("promisor object materialization requires protocol version 2")
    if not capabilities.supports("fetch"):
        raise RuntimeError("promisor remote does not advertise protocol-v2 fetch")

    body = build_fetch_request(
        capabilities,
        wanted,
        done=True,
        server_options=server_options,
    )
    parsed = client._post_fetch(body)
    if parsed.shallow or parsed.unshallow:
        raise RuntimeError("promisor object materialization unexpectedly changed shallow state")
    if parsed.pack is None:
        raise ValueError("promisor object materialization did not receive a packfile")
    return PackParser(parsed.pack).parse()


def _fetch_native_object(
    url: str,
    native_oid: str,
    *,
    server_options: Sequence[str] = (),
) -> Dict[str, NativeObject]:
    """Compatibility wrapper for the Phase213 single-object primitive."""
    return _fetch_native_objects(
        url,
        [native_oid],
        server_options=server_options,
    )


def _ordered_promisor_remotes(repo, recorded: Sequence[str]) -> Tuple[str, ...]:
    """Order recorded promisors by repository configuration, then by metadata.

    Git tries ordinary promisor remotes in configuration order. pygit's config
    model has no separate ``extensions.partialClone`` primary marker yet, so all
    recorded remotes participate in that order and any metadata-only names are
    retained at the end for compatible missing-remote error handling.
    """
    configured = tuple(repo.list_remotes())
    recorded_set = set(recorded)
    ordered = [name for name in configured if name in recorded_set]
    ordered.extend(name for name in recorded if name not in ordered)
    return tuple(ordered)


def materialize_promised_objects(
    pygit_dir: Path,
    native_oids: Iterable[str],
) -> Dict[str, str]:
    """Return native-SHA1 -> local-SHA256 mappings for promised objects.

    Already-resolved objects are metadata-only hits. Every remaining object is
    validated against the recorded promisor set. Promisor remotes are then tried
    in repository configuration order until every requested object is resolved
    or no usable remote remains. Each attempt asks for the complete still-missing
    set, preserving bulk-prefetch efficiency while allowing later remotes to fill
    gaps left by earlier caches.

    A one-object request deliberately retains Phase213's ``_fetch_native_object``
    call seam on every fallback attempt. Multi-object attempts use the Phase214
    batch request. Native Git SHA-1 remains confined to transport/promisor state;
    imported repository objects keep their content-derived SHA-256 identities.
    """
    pygit_dir = Path(pygit_dir)
    ordered = tuple(dict.fromkeys(_validate_native_oid(oid) for oid in native_oids))
    if not ordered:
        return {}

    resolved = resolved_native_objects(pygit_dir)
    result = {oid: resolved[oid] for oid in ordered if oid in resolved}
    unresolved = tuple(oid for oid in ordered if oid not in resolved)
    if not unresolved:
        return result

    kinds = {}
    for oid in unresolved:
        kind = promised_kind(pygit_dir, oid)
        if kind is None:
            raise PromisorMissingError(oid)
        kinds[oid] = kind

    recorded_remotes = _promisor_remotes_for_many(pygit_dir, unresolved)

    # Import lazily to avoid a module cycle during pygit's package bootstrap.
    from .repo import Repository

    repo = Repository(str(pygit_dir.parent))
    remotes = repo.list_remotes()
    remote_order = _ordered_promisor_remotes(repo, recorded_remotes)
    remaining = list(unresolved)
    attempted_configured_remote = False

    for remote in remote_order:
        if not remaining:
            break
        url = remotes.get(remote)
        if not url:
            continue
        attempted_configured_remote = True
        options = tuple(configured_server_options(repo, remote))
        try:
            if len(remaining) == 1:
                objects = _fetch_native_object(
                    url,
                    remaining[0],
                    server_options=options,
                )
            else:
                objects = _fetch_native_objects(
                    url,
                    remaining,
                    server_options=options,
                )
        except (RuntimeError, ValueError):
            # A cache-like promisor can legitimately be unable to satisfy a
            # request. Git's multi-promisor model falls through to the next
            # configured promisor rather than making that remote authoritative.
            continue

        available = [oid for oid in remaining if oid in objects]
        if not available:
            continue

        importer = TagPreservingNativeImporter(
            repo.store,
            objects,
            known=resolved_native_objects(pygit_dir),
        )
        newly_resolved = {oid: importer.import_oid(oid) for oid in available}
        update_promisor_state(pygit_dir, resolved=newly_resolved)
        result.update(newly_resolved)
        remaining = [oid for oid in remaining if oid not in newly_resolved]

    if remaining:
        first = remaining[0]
        # Preserve Phase212/213's intentional-missing contract both when all
        # owning remotes disappeared from config and when fallbacks were tried
        # but none could supply the object.
        raise PromisorMissingError(first, kinds[first])

    if not attempted_configured_remote:
        first = unresolved[0]
        raise PromisorMissingError(first, kinds[first])

    return result


def materialize_promised_object(pygit_dir: Path, native_oid: str) -> str:
    """Return the local SHA-256 identity for one promised native object."""
    native_oid = _validate_native_oid(native_oid)
    return materialize_promised_objects(pygit_dir, [native_oid])[native_oid]
