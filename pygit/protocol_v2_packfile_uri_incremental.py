"""Plan safe incremental ``have`` state from Git-compatible object maps.

Phase333 connects Phase328's local remote-tracking CAS plan to Phase332's
validated SHA-256/SHA-1 loose-object maps without performing network or repository
mutation.  A local tracking tip is advertised as a native ``have`` only when its
entire currently reachable local object closure exists, is readable, and has an
explicit validated compatibility mapping.

The returned native-to-local closure is intended for a later importer bridge so
objects omitted because they are reachable from a ``have`` can be resolved to
already-present local SHA-256 objects without synthesizing identities.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping, Optional

from .loose_object_map import read_loose_object_maps
from .objects.blob import BlobObject
from .objects.commit import CommitObject
from .objects.tree import TreeObject
from .protocol_v2_packfile_uri_tracking import PackfileUriRemoteTrackingPlan
from .refs import ZERO_SHA
from .repo import Repository


@dataclass(frozen=True)
class PackfileUriIncrementalState:
    """Read-only native negotiation state derived from verified local objects."""

    haves: tuple[str, ...]
    known_native_to_local: Dict[str, str]
    fallback_refs: tuple[str, ...]


def _mapping_indexes(repo: Repository) -> tuple[Dict[str, str], Dict[str, str]]:
    native_to_local: Dict[str, str] = {}
    local_to_native: Dict[str, str] = {}
    for object_map in read_loose_object_maps(repo):
        for native, local in object_map.native_to_local.items():
            previous_local = native_to_local.get(native)
            previous_native = local_to_native.get(local)
            if previous_local is not None and previous_local != local:
                raise ValueError("native SHA-1 maps to conflicting local SHA-256 ids")
            if previous_native is not None and previous_native != native:
                raise ValueError("local SHA-256 maps to conflicting native SHA-1 ids")
            native_to_local[native] = local
            local_to_native[local] = native
    return native_to_local, local_to_native


def _mapped_local_closure(
    repo: Repository,
    tip: str,
    local_to_native: Mapping[str, str],
) -> Optional[Dict[str, str]]:
    """Return native->local mappings for one complete local closure.

    ``None`` means the closure is valid local state but not fully represented in
    the compatibility map, so the caller must fall back to a non-incremental
    fetch for that tracking ref.  Missing/corrupt objects referenced by an
    already-mapped object are repository corruption and fail closed instead.
    """

    if tip not in local_to_native:
        return None

    known: Dict[str, str] = {}
    seen: set[str] = set()
    stack = [tip]
    first = True

    while stack:
        local = stack.pop()
        if local in seen:
            continue
        seen.add(local)

        native = local_to_native.get(local)
        if native is None:
            return None

        try:
            obj = repo.store.read(local)
        except KeyError as exc:
            raise RuntimeError(
                f"mapped local object {local} is missing while planning incremental fetch"
            ) from exc
        except Exception as exc:
            raise RuntimeError(
                f"mapped local object {local} is unreadable while planning incremental fetch"
            ) from exc

        if first:
            first = False
            if not isinstance(obj, CommitObject):
                raise ValueError(
                    "existing remote-tracking tip must resolve to a local commit before it can be a have"
                )

        known[native] = local

        if isinstance(obj, CommitObject):
            # Imported shallow commits intentionally retain native parents which
            # may not exist locally.  Do not auto-advertise them as complete
            # haves unless a later phase explicitly composes shallow negotiation.
            if obj.native_parents is not None:
                return None
            stack.append(obj.tree)
            stack.extend(obj.parents)
        elif isinstance(obj, TreeObject):
            for entry in obj.entries:
                if not entry.is_resolved:
                    return None
                stack.append(entry.sha)
        elif isinstance(obj, BlobObject):
            continue
        else:
            raise ValueError(
                f"unsupported local object type in incremental commit closure: {type(obj).__name__}"
            )

    return known


def plan_packfile_uri_incremental_state(
    repo: Repository,
    plan: PackfileUriRemoteTrackingPlan,
) -> PackfileUriIncrementalState:
    """Derive safe native ``have`` tips and importer-known mappings.

    New tracking refs and existing tips without a complete validated LMAP-backed
    closure are listed in ``fallback_refs`` and contribute no ``have``.  This is
    a normal full-fetch fallback, not an identity guess.

    If an LMAP claims a local object that is missing/corrupt, or an existing
    tracking tip is not a commit, the function fails closed because advertising
    such state as a ``have`` could cause a remote to omit required objects.
    """

    if not isinstance(repo, Repository):
        raise TypeError("incremental packfile-URI planning requires a Repository")
    if not isinstance(plan, PackfileUriRemoteTrackingPlan):
        raise TypeError("incremental packfile-URI planning requires a tracking plan")

    _, local_to_native = _mapping_indexes(repo)
    haves: set[str] = set()
    known: Dict[str, str] = {}
    fallback_refs: list[str] = []
    closure_cache: Dict[str, Optional[Dict[str, str]]] = {}

    for refname in sorted(plan.publications):
        publication = plan.publications[refname]
        old_local = publication.old_local_oid
        if old_local == ZERO_SHA:
            fallback_refs.append(refname)
            continue

        if old_local not in closure_cache:
            closure_cache[old_local] = _mapped_local_closure(
                repo,
                old_local,
                local_to_native,
            )
        closure = closure_cache[old_local]
        if closure is None:
            fallback_refs.append(refname)
            continue

        native_tip = local_to_native[old_local]
        haves.add(native_tip)
        for native, local in closure.items():
            previous = known.get(native)
            if previous is not None and previous != local:
                raise ValueError("incremental known-object mapping is contradictory")
            known[native] = local

    return PackfileUriIncrementalState(
        haves=tuple(sorted(haves)),
        known_native_to_local=dict(sorted(known.items())),
        fallback_refs=tuple(fallback_refs),
    )
