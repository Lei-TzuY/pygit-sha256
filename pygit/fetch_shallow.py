"""Protocol-v2 shallow fetch state and importer orchestration.

pygit stores repository-visible object identities as SHA-256 while smart HTTP
speaks native Git SHA-1. Existing shallow repositories therefore keep
``.pygit/shallow`` in local SHA-256 identity and translate boundaries only at
the transport edge.

Phase202 deliberately reuses the mature fetch/import pipeline. A shallow
request forces one transfer even when the selected tips are already present so
the server can return authoritative ``shallow-info`` updates. Phase204 switches
that importer seam to a stable native-parent representation, allowing genuinely
truncated shallow packs to be deepened without rewriting existing commit ids.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Dict, Iterator, Optional, Tuple

from .fetch_importer import StableShallowNativeImporter as NativeImporter
from .remote import Advertisement
from .repo import Repository


INFINITE_DEPTH = 2_147_483_647


@dataclass(frozen=True)
class ShallowFetchRequest:
    """One command-scoped shallow request expressed at the native boundary."""

    shallow: Tuple[str, ...]
    deepen: int
    deepen_relative: bool
    unshallow: bool


_ACTIVE_SHALLOW_REQUEST: ContextVar[Optional[ShallowFetchRequest]] = ContextVar(
    "pygit_shallow_fetch_request",
    default=None,
)


def current_shallow_request() -> Optional[ShallowFetchRequest]:
    return _ACTIVE_SHALLOW_REQUEST.get()


def read_shallow(repo: Repository) -> set[str]:
    """Read local SHA-256 shallow boundaries."""
    path = repo.pygit_dir / "shallow"
    if not path.exists():
        return set()
    result: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        oid = raw.strip().lower()
        if not oid:
            continue
        if len(oid) != 64:
            raise ValueError("malformed .pygit/shallow object id")
        try:
            int(oid, 16)
        except ValueError as exc:
            raise ValueError("malformed .pygit/shallow object id") from exc
        result.add(oid)
    return result


def write_shallow(repo: Repository, boundaries: set[str]) -> None:
    """Atomically replace local shallow boundaries, removing an empty file."""
    path = repo.pygit_dir / "shallow"
    if not boundaries:
        if path.exists():
            path.unlink()
        return
    tmp = path.with_name("shallow.lock")
    tmp.write_text("".join(f"{oid}\n" for oid in sorted(boundaries)), encoding="utf-8")
    tmp.replace(path)


def _native_boundaries(repo: Repository, remote: str, local: set[str]) -> Tuple[str, ...]:
    native_map = repo._read_native_map(remote)
    missing = sorted(oid for oid in local if oid not in native_map)
    if missing:
        raise RuntimeError(
            "shallow boundary has no native SHA-1 mapping for this remote; "
            "fetch the remote normally before deepening"
        )
    return tuple(sorted(native_map[oid] for oid in local))


def _apply_shallow_response(
    repo: Repository,
    result,
    known_by_native: Dict[str, str],
) -> None:
    """Translate v2 shallow-info back to local SHA-256 boundary identity."""
    shallow_native = tuple(getattr(result, "shallow", ()) or ())
    unshallow_native = tuple(getattr(result, "unshallow", ()) or ())
    if not shallow_native and not unshallow_native:
        return

    boundaries = read_shallow(repo)
    for native_oid in unshallow_native:
        local_oid = known_by_native.get(native_oid)
        if local_oid is None:
            raise RuntimeError(
                f"server unshallowed unknown native object {native_oid}"
            )
        boundaries.discard(local_oid)
    for native_oid in shallow_native:
        local_oid = known_by_native.get(native_oid)
        if local_oid is None:
            raise RuntimeError(
                f"server declared unknown shallow native object {native_oid}"
            )
        boundaries.add(local_oid)
    write_shallow(repo, boundaries)


def _fetch_import_sources_shallow(
    repo: Repository,
    client,
    advertisement: Advertisement,
    source_oids: Dict[str, str],
    native_map: Dict[str, str],
    known_by_native: Dict[str, str],
):
    """Force a shallow exchange and import even genuinely truncated histories.

    Existing pygit shallow repositories may retain the underlying converted
    object graph. Sending no ordinary ``have`` lines prevents those retained
    objects from incorrectly advertising history beyond the declared shallow
    boundary while the explicit ``shallow`` lines describe that boundary to the
    server. The stable shallow importer also accepts missing native parents and
    records them for lazy resolution when later deepen operations fetch them.
    """
    if not source_oids:
        return {}, 0

    selected_advertisement = Advertisement(
        refs=dict(source_oids),
        capabilities=set(advertisement.capabilities),
        symrefs=dict(advertisement.symrefs),
    )
    result = client.fetch(haves=[], advertisement=selected_advertisement)
    if result is None:
        raise RuntimeError("shallow fetch requires protocol version 2")

    importer = NativeImporter(repo.store, result.objects, known=known_by_native)
    imported = {
        ref_name: importer.import_oid(native_oid)
        for ref_name, native_oid in source_oids.items()
    }
    known_by_native.update(importer.converted)
    native_map.update(
        {
            pygit_sha: native_oid
            for native_oid, pygit_sha in importer.converted.items()
        }
    )
    _apply_shallow_response(repo, result, known_by_native)
    return imported, len(result.objects)


@contextmanager
def shallow_fetch_transport(
    repo: Repository,
    remote: str,
    *,
    depth: Optional[int] = None,
    deepen: Optional[int] = None,
    unshallow: bool = False,
) -> Iterator[None]:
    """Activate one protocol-v2 shallow/deepen request for a named remote."""
    selected = sum(value is not None for value in (depth, deepen)) + int(unshallow)
    if selected != 1:
        raise ValueError("exactly one of depth, deepen, or unshallow is required")
    if depth is not None and depth <= 0:
        raise ValueError("--depth must be a positive integer")
    if deepen is not None and deepen <= 0:
        raise ValueError("--deepen must be a positive integer")
    if remote not in repo.list_remotes():
        raise RuntimeError("shallow fetch controls currently require one named remote")

    local_boundaries = read_shallow(repo)
    if (deepen is not None or unshallow) and not local_boundaries:
        option = "--deepen" if deepen is not None else "--unshallow"
        raise RuntimeError(f"{option} requires an existing shallow repository")

    native = _native_boundaries(repo, remote, local_boundaries)
    request = ShallowFetchRequest(
        shallow=native,
        deepen=(
            INFINITE_DEPTH
            if unshallow
            else deepen
            if deepen is not None
            else int(depth)
        ),
        deepen_relative=deepen is not None,
        unshallow=unshallow,
    )

    # The modern configured and explicit fetch paths import the helper into
    # their own module namespaces, just like the Phase196 refetch seam.
    from . import fetch_configured, fetch_porcelain

    modules = (fetch_configured, fetch_porcelain)
    originals = [module._fetch_import_sources for module in modules]
    token = _ACTIVE_SHALLOW_REQUEST.set(request)
    try:
        for module in modules:
            module._fetch_import_sources = _fetch_import_sources_shallow
        yield
    finally:
        for module, original in zip(modules, originals):
            module._fetch_import_sources = original
        _ACTIVE_SHALLOW_REQUEST.reset(token)
