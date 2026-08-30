"""Lazy metadata-only refresh for unresolved promisor object sizes.

A filtered fetch normally enriches newly discovered promises through protocol-v2
``object-info size``.  That capability is optional, though, and an earlier
fetch may therefore leave some unresolved blobs without persisted size metadata.

This module gives later metadata-only consumers one safe retry path.  It queries
configured promisor remotes for sizes only, persists trustworthy answers, and
never falls back to content fetches.  Callers remain strict: if every candidate
remote is unavailable, lacks ``object-info``, returns malformed metadata, or
reports the object as unknown, the requested size simply remains absent.
"""

from __future__ import annotations

from typing import Dict, Iterator, Sequence
from weakref import WeakKeyDictionary

from .fetch_server_option_config import configured_server_options
from .promisor import promised_size, read_promisor_state, update_promisor_state
from .protocol_v2_object_info import SmartHttpV2ObjectInfoClient


OBJECT_INFO_SIZE_BATCH = 256

# Phase287 caches capability discovery inside one smart-HTTP client. Phase288
# keeps those clients alive for the lifetime of the Repository object so
# repeated metadata refreshes against the same effective remote configuration
# can reuse that negotiated capability state as well. Weak keys ensure this is
# process-local session state only and cannot keep repositories alive.
_OBJECT_INFO_CLIENTS: WeakKeyDictionary = WeakKeyDictionary()


def _normalize_native_oids(native_oids: Sequence[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in native_oids:
        oid = raw.lower()
        if len(oid) != 40 or any(ch not in "0123456789abcdef" for ch in oid):
            raise ValueError("promisor size refresh requires full native SHA-1 object ids")
        if oid in seen:
            continue
        seen.add(oid)
        normalized.append(oid)
    return tuple(normalized)


def _chunked_oids(native_oids: Sequence[str]) -> Iterator[tuple[str, ...]]:
    """Yield deterministic bounded object-info request batches."""

    if OBJECT_INFO_SIZE_BATCH <= 0:
        raise ValueError("object-info size batch must be positive")
    for start in range(0, len(native_oids), OBJECT_INFO_SIZE_BATCH):
        yield tuple(native_oids[start : start + OBJECT_INFO_SIZE_BATCH])


def _object_info_client(repo, remote: str, url: str, server_options: tuple[str, ...]):
    """Return a client scoped to one repository and effective remote config."""

    cache = _OBJECT_INFO_CLIENTS.setdefault(repo, {})
    key = (remote, url, server_options)
    client = cache.get(key)
    if client is None:
        client = SmartHttpV2ObjectInfoClient(url, server_options=server_options)
        cache[key] = client
    return client


def refresh_promisor_sizes(repo, native_oids: Sequence[str]) -> Dict[str, int]:
    """Best-effort refresh of missing promised-object sizes.

    Only unresolved promises that do not already have trusted size metadata are
    queried.  Promisor remotes are tried in deterministic name order; configured
    protocol-v2 server options are preserved for each remote.  Large pending sets
    are split into bounded deterministic ``object-info size`` requests so one
    partial clone cannot create an unbounded protocol request.  Clients are
    reused while the same Repository object and effective remote configuration
    remain alive, allowing Phase287's capability cache to span repeated refresh
    calls without persisting negotiation state on disk. Query failures are
    intentionally soft because the caller owns the final strictness policy.

    The returned mapping contains every requested OID whose trusted size is
    available after the refresh, whether it was already persisted or learned by
    this call.
    """

    requested = _normalize_native_oids(native_oids)
    if not requested:
        return {}

    state = read_promisor_state(repo.pygit_dir)
    promised = state["promised"]
    pending = {
        oid
        for oid in requested
        if oid in promised and promised_size(repo.pygit_dir, oid) is None
    }

    configured = repo.list_remotes()
    promisor_remotes = tuple(
        sorted(name for name in state["remotes"] if name in configured)
    )

    for remote in promisor_remotes:
        if not pending:
            break
        server_options = tuple(configured_server_options(repo, remote))
        client = _object_info_client(repo, remote, configured[remote], server_options)
        remote_failed = False
        for batch in _chunked_oids(tuple(sorted(pending))):
            try:
                sizes = client.query_sizes(batch)
            except (OSError, RuntimeError, ValueError):
                remote_failed = True
                break
            if not sizes:
                continue

            trusted = {
                oid: size
                for oid, size in sizes.items()
                if oid in pending and size is not None
            }
            if not trusted:
                continue
            update_promisor_state(repo.pygit_dir, sizes=trusted)
            pending.difference_update(trusted)

        if remote_failed:
            continue

    result: Dict[str, int] = {}
    for oid in requested:
        size = promised_size(repo.pygit_dir, oid)
        if size is not None:
            result[oid] = size
    return result
