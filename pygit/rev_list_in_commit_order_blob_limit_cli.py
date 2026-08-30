"""Compose ordered rev-list traversal with ``blob:limit`` filtering.

The ordered inventory remains authoritative for commit/snapshot order,
boundaries, object edges, and missing-object policy. Blob membership is decided
from local payload size or trusted promisor size metadata without materializing
an unresolved object merely to classify the filter.
"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

from . import rev_list_filter_blob_limit_cli as _blob_limit
from . import rev_list_in_commit_order_cli as _ordered
from . import rev_list_promisor_cli as _promisor
from .promisor_object_inventory import PromisorObjectInventoryEntry


_IN_COMMIT_ORDER = "--in-commit-order"
_FILTER_PROVIDED = "--filter-provided-objects"
_FILTER_PRINT_OMITTED = "--filter-print-omitted"


def _ordered_projection(argv: Sequence[str]) -> list[str]:
    return [
        arg
        for arg in argv
        if not arg.startswith("--filter=") and arg != _FILTER_PROVIDED
    ]


def _apply_blob_limit(
    repo,
    entries: Sequence[PromisorObjectInventoryEntry],
    *,
    limit: int,
) -> Tuple[PromisorObjectInventoryEntry, ...]:
    """Filter blobs by trusted uncompressed size without changing order."""

    _blob_limit._ensure_missing_blobs_are_classifiable(repo, entries)
    return tuple(
        entry
        for entry in entries
        if _blob_limit._entry_is_kept(repo, entry, limit=limit)
    )


def try_run_rev_list_in_commit_order_blob_limit(
    argv: Sequence[str],
) -> Optional[int]:
    """Handle ordered ``blob:limit=<n>[kmg]`` line/count traversal.

    Unresolved promised blobs are classified only when trusted promisor size
    metadata is present. Missing metadata remains a pre-render hard error, and
    this path never triggers content materialization for size classification.
    """

    if _IN_COMMIT_ORDER not in argv:
        return None
    limit = _blob_limit._blob_limit(argv)
    if limit is None:
        return None

    if "-z" in argv:
        raise ValueError(
            "rev-list --in-commit-order with --filter=blob:limit and -z is not yet supported"
        )
    if _FILTER_PRINT_OMITTED in argv:
        raise ValueError(
            "rev-list --in-commit-order with --filter=blob:limit and --filter-print-omitted is not yet supported"
        )

    projected = _ordered_projection(argv)
    parsed = _ordered._parse(projected)
    if parsed is None:
        raise RuntimeError("ordered rev-list parser declined blob:limit projection")

    repo = _promisor._find_repo()
    entries, boundary_oids = _ordered._ordered_inventory(repo, parsed)

    edges: Tuple[str, ...] = ()
    if parsed["in_commit_order_objects_edge"]:
        edges = _promisor._promisor_object_edges(
            repo,
            parsed["revisions"],
            all_refs=parsed["all_refs"],
            first_parent=parsed["first_parent"],
        )
    if edges and boundary_oids:
        entries, boundary_oids = _ordered._dedupe_edge_boundary_overlap(
            entries,
            boundary_oids=boundary_oids,
            edges=edges,
        )

    entries = _apply_blob_limit(repo, entries, limit=limit)
    return _ordered._render(
        entries,
        parsed=parsed,
        mode=parsed["in_commit_order_missing_mode"],
        boundary_oids=boundary_oids,
        edges=edges,
    )
