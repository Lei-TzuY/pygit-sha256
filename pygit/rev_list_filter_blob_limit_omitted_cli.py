"""Compose non-ordered ``blob:limit`` filtering with omitted-object output.

The ordinary blob-limit adapter owns metadata-only size classification and the
existing generic omission adapter owns Git's output ordering: traversal first,
then ``~<oid>`` omissions, missing diagnostics, and finally the optional count.
Phase281 composes those layers for line/count output without adding another
object walker. Phase283 extends that same composition to current Git's
structured ``-z`` object protocol now that Phase282 provides plain blob-limit
NUL traversal.

Unresolved promised blobs follow Phase280's cross-hash-domain rule. A trusted-
size promise that survives the filter remains in the missing-object channel. A
promise omitted by the filter has no genuine local SHA-256 identity and cannot
legally be printed as ``~<oid>`` until materialized, so the command fails before
emitting any output instead of exposing native SHA-1 or inventing a surrogate.

Git's ``-z`` protocol deliberately keeps omission records newline-framed while
present and missing object records are NUL-framed. Count mode suppresses present
object records, preserves omission-before-missing ordering, and leaves the final
integer newline-terminated. Phase283 reuses the mature shared NUL partitioners
rather than inventing an ``omitted=yes`` metadata token.
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from typing import Optional, Sequence

from . import rev_list_filter_blob_limit_cli as _blob_limit
from . import rev_list_filter_omitted_cli as _omitted
from . import rev_list_promisor_cli as _promisor
from .promisor_object_inventory import PromisorObjectInventoryEntry


_FILTER_PRINT_OMITTED = "--filter-print-omitted"
_IN_COMMIT_ORDER = "--in-commit-order"


def _unresolved_omitted_identity_error(entry: PromisorObjectInventoryEntry) -> RuntimeError:
    native = entry.native_oid or "<unknown>"
    return RuntimeError(
        "--filter-print-omitted cannot expose unresolved filtered promisor blob "
        f"{native} as a local SHA-256 id; materialize it first or omit "
        "--filter-print-omitted"
    )


def _omitted_local_oids(repo, entries, *, limit: int) -> tuple[str, ...]:
    """Return only genuine local SHA-256 blobs omitted by ``blob:limit``."""

    _blob_limit._ensure_missing_blobs_are_classifiable(repo, entries)
    omitted: list[str] = []
    for entry in entries:
        if entry.type_name != "blob":
            continue

        if entry.missing:
            if _blob_limit._entry_is_kept(repo, entry, limit=limit):
                continue
            raise _unresolved_omitted_identity_error(entry)

        if entry.oid is None:
            raise RuntimeError("present blob inventory entry has no local SHA-256 identity")
        size = _blob_limit._local_blob_size(repo, entry.oid)
        if size is None:
            raise RuntimeError(
                f"present blob {entry.oid} cannot be read for size filtering"
            )
        if size < limit:
            continue

        oid = entry.oid.lower()
        if len(oid) != 64 or any(ch not in "0123456789abcdef" for ch in oid):
            raise RuntimeError("omitted local object has no valid SHA-256 identity")
        omitted.append(oid)
    return tuple(omitted)


def try_run_rev_list_blob_limit_filter_print_omitted(
    argv: Sequence[str],
) -> Optional[int]:
    """Handle non-ordered ``blob:limit + --filter-print-omitted`` output."""

    if _IN_COMMIT_ORDER in argv or _FILTER_PRINT_OMITTED not in argv:
        return None
    if argv.count(_FILTER_PRINT_OMITTED) != 1:
        raise ValueError("rev-list accepts --filter-print-omitted at most once")

    limit = _blob_limit._blob_limit(argv)
    if limit is None:
        return None

    cleaned = [arg for arg in argv if arg != _FILTER_PRINT_OMITTED]
    projected = _blob_limit._project(cleaned)
    repo = _promisor._find_repo()
    _parsed, _boundary_commits, entries = _blob_limit._inventory_context(
        repo, projected
    )
    omitted = _omitted_local_oids(repo, entries, limit=limit)

    capture = io.StringIO()
    with redirect_stdout(capture):
        code = _blob_limit.try_run_rev_list_blob_limit(cleaned)
    if code is None:
        raise RuntimeError("rev-list blob:limit adapter declined omitted-object projection")

    projected_output = capture.getvalue()
    if "-z" in cleaned:
        count_line: Optional[str] = None
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
