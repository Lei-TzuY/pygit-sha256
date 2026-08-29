"""Metadata-only ``rev-list --filter=blob:none`` adapter.

The promisor-aware rev-list paths already know how to traverse partial-clone
metadata without materializing promised blobs.  This adapter composes Git's
``blob:none`` object filter with those paths instead of falling back to the
historical object walker, where touching a foreign tree entry could trigger a
lazy fetch.

Phase246 introduced line-oriented filtering for the missing-object traversal.
Phase247 extends the same projection to ``--count``.  Count mode deliberately
runs the established uncounted traversal first, filters that exact object
stream, preserves advertised object-edge and missing records, then counts only
the filtered present objects.  This mirrors native Git while keeping boundary
and edge selection authoritative in the existing rev-list layers.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from typing import Optional, Sequence

from . import rev_list_missing_print_cli as _missing_print
from . import rev_list_promisor_cli as _promisor
from .objects import BlobObject
from .promisor import promised_kind


def _filter_spec(argv: Sequence[str]) -> Optional[str]:
    filters = [arg for arg in argv if arg.startswith("--filter=")]
    if not filters:
        return None
    if len(filters) != 1:
        raise ValueError("rev-list accepts exactly one --filter action in this phase")
    spec = filters[0].split("=", 1)[1]
    if spec != "blob:none":
        raise ValueError("pygit currently supports --filter=blob:none with --missing")
    return spec


def _project(argv: Sequence[str]) -> list[str]:
    projected = [arg for arg in argv if not arg.startswith("--filter=")]
    if "-z" in projected:
        raise ValueError("--filter=blob:none with -z is not yet supported")
    missing = [arg for arg in projected if arg.startswith("--missing=")]
    if len(missing) != 1 or missing[0] not in {
        "--missing=allow-promisor",
        "--missing=print",
        "--missing=print-info",
    }:
        raise ValueError(
            "--filter=blob:none currently requires --missing=allow-promisor, print, or print-info"
        )
    return projected


def _run_projected(argv: Sequence[str]) -> tuple[int, tuple[str, ...]]:
    capture = io.StringIO()
    with redirect_stdout(capture):
        code = _missing_print.try_run_rev_list_missing_print(argv)
        if code is None:
            code = _promisor.try_run_rev_list_allow_promisor(argv)
    if code is None:
        raise RuntimeError("promisor rev-list adapter declined blob:none projection")
    return code, tuple(capture.getvalue().splitlines())


def _without_count(argv: Sequence[str]) -> list[str]:
    """Return the same traversal request with count presentation removed."""

    return [arg for arg in argv if arg != "--count"]


def _is_blob_line(repo, line: str) -> bool:
    if not line:
        return False

    token = line.split(None, 1)[0]
    if token.startswith("?"):
        native_oid = token[1:].lower()
        return promised_kind(repo.pygit_dir, native_oid) == "blob"

    if token.startswith("-"):
        token = token[1:]
    oid = token.lower()
    if len(oid) != 64 or any(ch not in "0123456789abcdef" for ch in oid):
        return False
    try:
        return isinstance(repo.store.read(oid), BlobObject)
    except (FileNotFoundError, KeyError):
        # The metadata-only --missing paths should never expose a missing local
        # SHA-256 as a present record.  Keep the line so the downstream
        # integrity semantics remain visible instead of silently hiding damage.
        return False


def _explicit_object_edges(repo, argv: Sequence[str]) -> frozenset[str]:
    """Return explicit ``--objects-edge`` commits for count classification.

    A leading ``-`` record is not sufficient to identify an object edge: under
    ``--reverse --boundary`` a genuine limit-induced boundary may itself be the
    first output record and must still contribute to the object count.  Reuse
    Phase234's metadata-only edge planner so only actual exclusion edges are
    removed from the final count.
    """

    if "--objects-edge" not in argv:
        return frozenset()

    if "--missing=print" in argv:
        parse_argv = _missing_print._objects_projection(argv, plain=True)
    elif "--missing=print-info" in argv:
        parse_argv = _missing_print._objects_projection(argv, plain=False)
    else:
        parse_argv = ["--objects" if arg == "--objects-edge" else arg for arg in argv]

    parsed = _promisor._parse_allow_promisor(parse_argv)
    if parsed is None:
        raise RuntimeError("promisor parser declined blob:none object-edge projection")

    return frozenset(
        _promisor._promisor_object_edges(
            repo,
            parsed["revisions"],
            all_refs=parsed["all_refs"],
            first_parent=parsed["first_parent"],
        )
    )


def _render_filtered_count(lines: Sequence[str], *, edge_oids: frozenset[str]) -> None:
    """Render non-count records and print the filtered present-object count."""

    present_count = 0
    for line in lines:
        if line.startswith("?"):
            # Git's missing=print family keeps missing records visible under
            # --count, but missing objects do not contribute to the integer.
            print(line)
            continue

        token = line.split(None, 1)[0] if line else ""
        if token.startswith("-") and token[1:].lower() in edge_oids:
            # --objects-edge advertises excluded commits even under --count;
            # the excluded edge itself is not part of the selected object set.
            print(line)
            continue

        # Selected commits, trees, path-bearing commit objects, and genuine
        # boundary commits are present objects after blob:none filtering.
        present_count += 1

    print(present_count)


def try_run_rev_list_filter(argv: Sequence[str]) -> Optional[int]:
    """Handle ``--filter=blob:none`` for metadata-only missing-object traversal."""

    if _filter_spec(argv) is None:
        return None

    projected = _project(argv)
    count = "--count" in projected
    traversal_argv = _without_count(projected) if count else projected
    code, lines = _run_projected(traversal_argv)
    repo = _promisor._find_repo()
    filtered = tuple(line for line in lines if not _is_blob_line(repo, line))

    if code:
        for line in filtered:
            print(line)
        return code

    if count:
        _render_filtered_count(
            filtered,
            edge_oids=_explicit_object_edges(repo, traversal_argv),
        )
        return code

    for line in filtered:
        print(line)
    return code
