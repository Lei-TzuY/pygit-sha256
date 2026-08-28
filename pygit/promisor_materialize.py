"""Lazy materialization for objects promised by a partial-fetch remote.

Phase212 records omitted native Git objects without inventing local SHA-256
identities.  Phase213 turns that durable promise into a read-time capability:
when a consumer actually needs one of those native objects, fetch exactly that
object over protocol v2, import it into the SHA-256 store, and persist the
native->local resolution.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Sequence

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


def _fetch_native_object(
    url: str,
    native_oid: str,
    *,
    server_options: Sequence[str] = (),
) -> Dict[str, NativeObject]:
    """Fetch one promised native object without applying the repository filter."""
    client = SmartHttpV2FetchClient(url, server_options=server_options)
    capabilities = client.discover_capabilities()
    if capabilities is None:
        raise RuntimeError("promisor object materialization requires protocol version 2")
    if not capabilities.supports("fetch"):
        raise RuntimeError("promisor remote does not advertise protocol-v2 fetch")

    body = build_fetch_request(
        capabilities,
        [native_oid],
        done=True,
        server_options=server_options,
    )
    parsed = client._post_fetch(body)
    if parsed.shallow or parsed.unshallow:
        raise RuntimeError("promisor object materialization unexpectedly changed shallow state")
    if parsed.pack is None:
        raise ValueError("promisor object materialization did not receive a packfile")
    return PackParser(parsed.pack).parse()


def materialize_promised_object(pygit_dir: Path, native_oid: str) -> str:
    """Return the local SHA-256 identity for one promised native object.

    Resolved objects are a metadata-only fast path.  Otherwise the object is
    requested by native SHA-1 from the single recorded promisor remote, imported
    through the normal native->SHA-256 conversion boundary, and atomically moved
    from ``promised`` to ``resolved`` in ``promisor.json``.
    """
    pygit_dir = Path(pygit_dir)
    native_oid = _validate_native_oid(native_oid)

    resolved = resolved_native_objects(pygit_dir)
    existing = resolved.get(native_oid)
    if existing:
        return existing

    kind = promised_kind(pygit_dir, native_oid)
    if kind is None:
        raise PromisorMissingError(native_oid)

    remote = _promisor_remote(pygit_dir, native_oid)

    # Import lazily to avoid a module cycle during pygit's package bootstrap.
    from .repo import Repository

    repo = Repository(str(pygit_dir.parent))
    remotes = repo.list_remotes()
    url = remotes.get(remote)
    if not url:
        # Keep Phase212's missing-object behavior when promisor metadata exists
        # but its owning remote is no longer configured.  The object remains a
        # known intentional omission, just not one that can currently be filled.
        raise PromisorMissingError(native_oid, kind)

    options = tuple(configured_server_options(repo, remote))
    objects = _fetch_native_object(url, native_oid, server_options=options)
    if native_oid not in objects:
        raise ValueError(
            f"promisor remote response did not contain requested {kind} {native_oid}"
        )

    importer = TagPreservingNativeImporter(
        repo.store,
        objects,
        known=resolved_native_objects(pygit_dir),
    )
    local_oid = importer.import_oid(native_oid)
    update_promisor_state(pygit_dir, resolved={native_oid: local_oid})
    return local_oid
