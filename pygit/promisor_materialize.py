"""Lazy materialization for objects promised by partial-fetch remotes.

Phase212 records omitted native Git objects without inventing local SHA-256
identities. Phase213 added single-object read-time materialization. Phase214
extends that primitive with a batched form so initial partial-clone checkout can
resolve all blobs needed by the selected worktree in one protocol-v2 request.

Phase221 removes the historical single-promisor restriction. Missing objects are
attempted against configured promisor remotes in deterministic repository config
order, shrinking the unresolved set after every successful remote.

Phase222 completes Git's primary-promisor ordering rule. Configured promisor
remotes are discovered from ``remote.<name>.promisor`` and
``remote.<name>.partialCloneFilter`` in addition to the persistent promisor
sidecar, while ``extensions.partialClone`` names the primary remote that must be
tried last. This allows cache/mirror promisors to satisfy missing objects before
the canonical partial-clone source without changing repository-visible SHA-256
identity.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, Optional, Sequence, Tuple

from .config import GitConfig
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


def _recorded_promisor_remotes(
    pygit_dir: Path,
    native_oids: Sequence[str],
) -> Tuple[str, ...]:
    """Validate promised OIDs and return sidecar-recorded promisor names.

    Public multi-promisor materialization may legitimately receive an empty
    tuple here: Git config can identify promisor remotes even when the sidecar
    only records the promised object set. Legacy single-owner helpers layer their
    historical non-empty/exactly-one constraints on top of this primitive.
    """
    if not native_oids:
        raise ValueError("promisor materialization requires at least one object id")
    state = read_promisor_state(pygit_dir)
    for native_oid in native_oids:
        if native_oid not in state["promised"]:
            raise PromisorMissingError(native_oid)
    return tuple(state["remotes"])


def _promisor_remotes_for_many(pygit_dir: Path, native_oids: Sequence[str]) -> Tuple[str, ...]:
    """Legacy helper requiring at least one promisor recorded in sidecar state."""
    remotes = _recorded_promisor_remotes(pygit_dir, native_oids)
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


def _is_true_config(value: Optional[str]) -> bool:
    """Return whether a Git-style boolean config value is explicitly true."""
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _ordered_promisor_remotes(repo, recorded: Sequence[str]) -> Tuple[str, ...]:
    """Return promisor remotes in Git-compatible missing-object fallback order.

    Candidates come from three sources:

    * remotes already recorded in ``promisor.json``;
    * ``remote.<name>.promisor=true`` or a configured
      ``remote.<name>.partialCloneFilter``;
    * the primary remote named by ``extensions.partialClone``.

    Ordinary candidates follow repository remote configuration order. Git treats
    the ``extensions.partialClone`` remote specially and tries it last, because
    cache/mirror promisors are expected to be preferable when available. A stale
    metadata/config name is retained in the logical order but is skipped later if
    its URL no longer exists.
    """
    configured = tuple(repo.list_remotes())
    config = GitConfig(repo.pygit_dir)
    primary = config.get("extensions", "partialClone")
    primary = primary.strip() if primary else None

    candidates = set(recorded)
    for name in configured:
        if _is_true_config(config.get("remote", f"{name}.promisor")):
            candidates.add(name)
        if config.get("remote", f"{name}.partialCloneFilter") is not None:
            candidates.add(name)
    if primary:
        candidates.add(primary)

    ordered = [
        name
        for name in configured
        if name in candidates and name != primary
    ]
    ordered.extend(
        name
        for name in recorded
        if name not in ordered and name != primary
    )
    if primary and primary not in ordered:
        ordered.append(primary)
    return tuple(ordered)


def materialize_promised_objects(
    pygit_dir: Path,
    native_oids: Iterable[str],
) -> Dict[str, str]:
    """Return native-SHA1 -> local-SHA256 mappings for promised objects.

    Already-resolved objects are metadata-only hits. Every remaining object is
    validated against the persistent promise set. Promisor remotes are then tried
    in Git-compatible order until every requested object is resolved or no usable
    remote remains. Ordinary/cache promisors are tried first; the primary remote
    named by ``extensions.partialClone`` is tried last. Each attempt asks for the
    complete still-missing set, preserving bulk-prefetch efficiency while later
    remotes fill gaps left by earlier caches.

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

    recorded_remotes = _recorded_promisor_remotes(pygit_dir, unresolved)

    # Import lazily to avoid a module cycle during pygit's package bootstrap.
    from .repo import Repository

    repo = Repository(str(pygit_dir.parent))
    remotes = repo.list_remotes()
    remote_order = _ordered_promisor_remotes(repo, recorded_remotes)
    configured_candidates = tuple(remote for remote in remote_order if remotes.get(remote))
    if not configured_candidates:
        first = unresolved[0]
        raise PromisorMissingError(first, kinds[first])

    remaining = list(unresolved)
    for remote in configured_candidates:
        if not remaining:
            break
        url = remotes[remote]
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
        raise PromisorMissingError(first, kinds[first])

    return result


def materialize_promised_object(pygit_dir: Path, native_oid: str) -> str:
    """Return the local SHA-256 identity for one promised native object."""
    native_oid = _validate_native_oid(native_oid)
    return materialize_promised_objects(pygit_dir, [native_oid])[native_oid]
