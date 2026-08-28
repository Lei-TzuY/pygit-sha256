"""Command-scoped Git-style ``fetch --refetch`` transport policy.

Git's ``--refetch`` asks the server for the selected object graph without
using local ``have`` objects to negotiate a thin incremental transfer.  pygit
keeps the repository SHA-256-native, so this helper only changes the native
SHA-1 smart-HTTP negotiation boundary and then reuses the normal importer.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Dict, Iterator, Tuple

from .fetch_configured import TagPreservingNativeImporter as NativeImporter
from .remote import Advertisement


def _force_fetch_import_sources(
    repo,
    client,
    advertisement: Advertisement,
    source_oids: Dict[str, str],
    native_map: Dict[str, str],
    known_by_native: Dict[str, str],
) -> Tuple[Dict[str, str], int]:
    """Import selected refs while deliberately sending an empty have set."""
    if not source_oids:
        return {}, 0

    selected_advertisement = Advertisement(
        refs=dict(source_oids),
        capabilities=set(advertisement.capabilities),
        symrefs=dict(advertisement.symrefs),
    )
    result = client.fetch(haves=[], advertisement=selected_advertisement)
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
    return imported, len(result.objects)


@contextmanager
def refetch_transport() -> Iterator[None]:
    """Temporarily replace fetch import negotiation with no-have negotiation.

    The established configured, explicit-refspec, direct-URL, prefetch, and
    multi-remote paths import ``_fetch_import_sources`` into their own module
    namespace.  Patch those seams for one command invocation, then restore them
    even when fetch raises.  pygit's CLI executes fetch synchronously, so this
    command-local scope cannot leak into another command process.
    """
    from . import fetch_configured, fetch_direct, fetch_porcelain

    modules = (fetch_configured, fetch_porcelain, fetch_direct)
    originals = [module._fetch_import_sources for module in modules]
    try:
        for module in modules:
            module._fetch_import_sources = _force_fetch_import_sources
        yield
    finally:
        for module, original in zip(modules, originals):
            module._fetch_import_sources = original
