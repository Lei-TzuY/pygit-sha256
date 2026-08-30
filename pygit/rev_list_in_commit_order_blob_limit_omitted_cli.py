"""Compose ordered ``blob:limit`` filtering with omitted-object framing.

Phase267 established metadata-only ordered blob-size membership on the current
rev-list stack. Phase268 adds Git's independent ``~<oid>`` omission channel
without adding another object walker: the same ordered inventory determines
traversal order, edge/boundary presentation, surviving membership, and omitted
local SHA-256 identities. Phase269 extends that composition to Git's structured
``-z`` object protocol while preserving upstream's deliberately mixed framing:
NUL traversal records, newline-framed omissions, then NUL missing records.
Phase270 additionally preserves native ``-z + --count`` ordering, with a final
newline-terminated integer after omissions and missing diagnostics.
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from typing import Optional, Sequence, Tuple

from . import rev_list_filter_blob_limit_cli as _blob_limit
from . import rev_list_filter_omitted_cli as _omitted
from . import rev_list_in_commit_order_blob_limit_cli as _ordered_blob_limit
from . import rev_list_in_commit_order_cli as _ordered
from . import rev_list_promisor_cli as _promisor
from .promisor_object_inventory import PromisorObjectInventoryEntry


_IN_COMMIT_ORDER = "--in-commit-order"
_FILTER_PRINT_OMITTED = "--filter-print-omitted"


def _partition_blob_limit(
    repo,
    entries: Sequence[PromisorObjectInventoryEntry],
    *,
    limit: int,
) -> Tuple[Tuple[PromisorObjectInventoryEntry, ...], Tuple[str, ...]]:
    """Return surviving entries and genuine local SHA-256 omitted blob ids."""

    _blob_limit._ensure_missing_blobs_are_classifiable(entries)

    surviving: list[PromisorObjectInventoryEntry] = []
    omitted: list[str] = []
    for entry in entries:
        if entry.type_name != "blob":
            surviving.append(entry)
            continue

        if entry.oid is None:
            native = entry.native_oid or "<unknown>"
            raise RuntimeError(
                "--filter=blob:limit cannot classify unresolved promised blob "
                f"{native}: persistent promisor size metadata is unavailable"
            )

        size = _blob_limit._local_blob_size(repo, entry.oid)
        if size is None:
            raise RuntimeError(
                f"present blob {entry.oid} cannot be read for size filtering"
            )
        if size < limit:
            surviving.append(entry)
            continue

        oid = entry.oid.lower()
        if len(oid) != 64 or any(ch not in "0123456789abcdef" for ch in oid):
            raise RuntimeError("omitted local object has no valid SHA-256 identity")
        omitted.append(oid)

    return tuple(surviving), tuple(omitted)


def try_run_rev_list_in_commit_order_blob_limit_omitted(
    argv: Sequence[str],
) -> Optional[int]:
    """Render ordered blob-limit traversal, omissions, missing, then count."""

    if _IN_COMMIT_ORDER not in argv or _FILTER_PRINT_OMITTED not in argv:
        return None
    if argv.count(_FILTER_PRINT_OMITTED) != 1:
        raise ValueError("rev-list accepts --filter-print-omitted at most once")

    limit = _blob_limit._blob_limit(argv)
    if limit is None:
        return None

    cleaned = [arg for arg in argv if arg != _FILTER_PRINT_OMITTED]
    projected = _ordered_blob_limit._ordered_projection(cleaned)
    parsed = _ordered._parse(projected)
    if parsed is None:
        raise RuntimeError("ordered rev-list parser declined blob:limit omission projection")

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

    entries, omitted = _partition_blob_limit(repo, entries, limit=limit)

    capture = io.StringIO()
    with redirect_stdout(capture):
        code = _ordered._render(
            entries,
            parsed=parsed,
            mode=parsed["in_commit_order_missing_mode"],
            boundary_oids=boundary_oids,
            edges=edges,
        )
    if code:
        sys.stdout.write(capture.getvalue())
        return code

    projected_output = capture.getvalue()
    if parsed["in_commit_order_nul"]:
        count_line = None
        if "--count" in cleaned:
            traversal, missing, count_line = _omitted._partition_projected_nul_count(
                projected_output
            )
        else:
            traversal, missing = _omitted._partition_projected_nul(projected_output)
        for record in traversal:
            sys.stdout.write(record)
        for oid in omitted:
            sys.stdout.write(f"~{oid}\n")
        for record in missing:
            sys.stdout.write(record)
        if count_line is not None:
            sys.stdout.write(f"{count_line}\n")
        return code

    traversal, missing, count_line = _omitted._partition_projected_lines(
        projected_output.splitlines(),
        count_mode="--count" in cleaned,
    )
    for line in traversal:
        print(line)
    for oid in omitted:
        print(f"~{oid}")
    for line in missing:
        print(line)
    if count_line is not None:
        print(count_line)
    return code
