"""Lazy materialization for objects promised by a partial-fetch remote.

Phase212 records omitted native Git objects without inventing local SHA-256
identities. Phase213 added single-object read-time materialization. Phase214
extends that primitive with a batched form so initial partial-clone checkout can
resolve all blobs needed by the selected worktree in one protocol-v2 request.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, Sequence

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
    """Return the only remote that can own Phase212's global promise set."""
    state = read_promisor_state(pygit_dir)
    if native_oid not in state["promised"]:
        raise PromisorMissingError(native_oid)
    remotes = list(state["remotes"])
    if len(remotes) != 1:
        raise RuntimeError(
            "cannot materialize promisor object: repository does not identify exactly one promisor remote"
        )
    return remotes[0]


def _promisor_remote_for_many(pygit_dir: Path, native_oids: Sequence[str]) -> str:
    """Validate that every unresolved oid belongs to the single promisor remote."""
    if not native_oids:
        raise ValueError("promisor materialization requires at least one object id")
    state = read_promisor_state(pygit_dir)
    for native_oid in native_oids:
        if native_oid not in state["promised"]:
            raise PromisorMissingError(native_oid)
    remotes = list(state["remotes"])
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


def materialize_promised_objects(
    pygit_dir: Path,
    native_oids: Iterable[str],
) -> Dict[str, str]:
    """Return native-SHA1 -> local-SHA256 mappings for promised objects.

    Already-resolved objects are metadata-only hits. Every remaining object is
    validated against the single promisor remote, fetched together in one v2
    request, imported into the real SHA-256 object store, and atomically moved
    from ``promised`` to ``resolved`` in ``promisor.json``.
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

    remote = _promisor_remote_for_many(pygit_dir, unresolved)

    # Import lazily to avoid a module cycle during pygit's package bootstrap.
    from .repo import Repository

    repo = Repository(str(pygit_dir.parent))
    remotes = repo.list_remotes()
    url = remotes.get(remote)
    if not url:
        # Preserve Phase212/213's intentional-missing error contract when the
        # promisor metadata survives but its owning remote is no longer configured.
        first = unresolved[0]
        raise PromisorMissingError(first, kinds[first])

    options = tuple(configured_server_options(repo, remote))
    objects = _fetch_native_objects(url, unresolved, server_options=options)
    for oid in unresolved:
        if oid not in objects:
            raise ValueError(
                f"promisor remote response did not contain requested {kinds[oid]} {oid}"
            )

    importer = TagPreservingNativeImporter(
        repo.store,
        objects,
        known=resolved_native_objects(pygit_dir),
    )
    newly_resolved = {oid: importer.import_oid(oid) for oid in unresolved}
    update_promisor_state(pygit_dir, resolved=newly_resolved)
    result.update(newly_resolved)
    return result


def materialize_promised_object(pygit_dir: Path, native_oid: str) -> str:
    """Return the local SHA-256 identity for one promised native object."""
    native_oid = _validate_native_oid(native_oid)
    return materialize_promised_objects(pygit_dir, [native_oid])[native_oid]
