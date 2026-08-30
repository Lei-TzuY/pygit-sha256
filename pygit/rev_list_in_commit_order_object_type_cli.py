"""Compose ``rev-list --in-commit-order`` with ``object:type`` filters.

Phase264 established a structured commit/snapshot-interleaved inventory for
ordered object traversal.  Phase266 applies Git's ``object:type`` membership
rules directly to that inventory instead of reparsing rendered lines or adding a
second walker.  Explicit positive roots preserve Git's default provided-object
exemption unless ``--filter-provided-objects`` is requested; object-edge records
remain an independent presentation channel.
"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

from . import rev_list_filter_cli as _filter
from . import rev_list_in_commit_order_cli as _ordered
from . import rev_list_promisor_cli as _promisor
from .promisor_object_inventory import PromisorObjectInventoryEntry


_IN_COMMIT_ORDER = "--in-commit-order"
_FILTER_PROVIDED = "--filter-provided-objects"
_FILTER_PRINT_OMITTED = "--filter-print-omitted"


def _requested_type(argv: Sequence[str]) -> Optional[str]:
    if _IN_COMMIT_ORDER not in argv:
        return None
    filters = [arg for arg in argv if arg.startswith("--filter=")]
    if not filters:
        return None
    if len(filters) != 1:
        raise ValueError(
            "rev-list --in-commit-order accepts exactly one --filter action in this phase"
        )
    spec = _filter._filter_spec(argv)
    if spec is None or not spec.startswith("object:type="):
        return None
    return spec.split("=", 2)[2]


def _ordered_projection(argv: Sequence[str]) -> list[str]:
    """Remove only Phase266-owned filter presentation arguments."""

    return [
        arg
        for arg in argv
        if not arg.startswith("--filter=")
        and arg not in {_FILTER_PROVIDED, _FILTER_PRINT_OMITTED}
    ]


def _keep_entry(
    entry: PromisorObjectInventoryEntry,
    *,
    requested: str,
    provided: frozenset[str],
) -> bool:
    """Apply Git object:type membership to one structured inventory entry."""

    if (
        entry.type_name == "commit"
        and entry.path is None
        and entry.oid is not None
        and entry.oid.lower() in provided
    ):
        return True
    return entry.type_name == requested


def _apply_object_type(
    entries: Sequence[PromisorObjectInventoryEntry],
    *,
    requested: str,
    provided: frozenset[str],
) -> Tuple[PromisorObjectInventoryEntry, ...]:
    return tuple(
        entry
        for entry in entries
        if _keep_entry(entry, requested=requested, provided=provided)
    )


def try_run_rev_list_in_commit_order_object_type(
    argv: Sequence[str],
) -> Optional[int]:
    """Handle ordered ``object:type=commit|tree|blob`` traversal.

    ``object:type`` filters do not populate Git's omitted-object set, so
    ``--filter-print-omitted`` is accepted here but intentionally adds no
    ``~<oid>`` records.  Filtering happens before ordinary missing-object
    validation, allowing a requested type to discard promised objects of other
    known types without materialization.
    """

    requested = _requested_type(argv)
    if requested is None:
        return None
    if argv.count(_FILTER_PRINT_OMITTED) > 1:
        raise ValueError("rev-list accepts --filter-print-omitted at most once")

    filter_provided = _FILTER_PROVIDED in argv
    projected = _ordered_projection(argv)
    parsed = _ordered._parse(projected)
    if parsed is None:
        raise RuntimeError("ordered rev-list parser declined object:type projection")

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

    provided = (
        frozenset()
        if filter_provided
        else _filter._provided_commit_roots(repo, parsed)
    )
    entries = _apply_object_type(
        entries,
        requested=requested,
        provided=provided,
    )

    return _ordered._render(
        entries,
        parsed=parsed,
        mode=parsed["in_commit_order_missing_mode"],
        boundary_oids=boundary_oids,
        edges=edges,
    )
