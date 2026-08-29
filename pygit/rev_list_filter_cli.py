"""Metadata-only ``rev-list --filter=blob:none`` adapter.

The promisor-aware rev-list paths already know how to traverse partial-clone
metadata without materializing promised blobs. This adapter composes Git's
``blob:none`` object filter with those paths instead of falling back to the
historical object walker, where touching a foreign tree entry could trigger a
lazy fetch.

Phase246 introduced line-oriented filtering for the missing-object traversal.
Phase247 extends the same projection to ``--count`` without reimplementing
boundary or object-edge semantics: the established count path remains
canonical, while a second metadata-only inspection traversal determines how
many already-present blobs must be subtracted from that authoritative count.
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


def _inspection_projection(argv: Sequence[str]) -> list[str]:
    """Expose the complete selected/boundary object closure for blob counting.

    The Phase240/243 ``--count`` implementations intentionally suppress normal
    present-object records and may synthesize boundary counts directly from the
    inventory.  Therefore filtered count cannot be reconstructed from the
    textual count output alone.  For inspection only, remove ``--count`` and
    project ``--objects-edge`` to ``--objects``.  Revision exclusions remain
    unchanged, while Phase236's boundary snapshot-root planner stays active.
    """

    result: list[str] = []
    for arg in argv:
        if arg == "--count":
            continue
        if arg == "--objects-edge":
            result.append("--objects")
        else:
            result.append(arg)
    return result


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
        # SHA-256 as a present record. Keep the line so downstream integrity
        # semantics remain visible instead of silently hiding damage.
        return False


def _present_blob_count(repo, lines: Sequence[str]) -> int:
    """Count already-local blobs which contribute to the unfiltered integer."""

    return sum(
        1
        for line in lines
        if not line.startswith("?") and _is_blob_line(repo, line)
    )


def _render_adjusted_count(
    repo,
    count_lines: Sequence[str],
    inspection_lines: Sequence[str],
) -> None:
    """Filter preserved count records and subtract present blobs from the tail."""

    if not count_lines:
        raise RuntimeError("rev-list count projection produced no output")
    try:
        raw_count = int(count_lines[-1])
    except ValueError as exc:
        raise RuntimeError("rev-list count projection did not end with an integer") from exc

    adjusted = raw_count - _present_blob_count(repo, inspection_lines)
    if adjusted < 0:
        raise RuntimeError("blob:none subtraction exceeded the projected object count")

    # Under --count, Git still advertises object edges and print-family missing
    # records. Remove only blob promises from that framing; explicit edges and
    # non-blob missing records remain authoritative from the existing renderer.
    for line in count_lines[:-1]:
        if not _is_blob_line(repo, line):
            print(line)
    print(adjusted)


def try_run_rev_list_filter(argv: Sequence[str]) -> Optional[int]:
    """Handle ``--filter=blob:none`` for metadata-only missing-object traversal."""

    if _filter_spec(argv) is None:
        return None

    projected = _project(argv)
    count = "--count" in projected

    if count:
        # Keep Phase240/243's count implementation authoritative for selected
        # commits, boundary commits, edge/boundary overlap, limits, and reverse
        # ordering. A separate --objects inspection pass is used solely to find
        # present blobs that blob:none removes from that numeric result.
        code, count_lines = _run_projected(projected)
        if code:
            repo = _promisor._find_repo()
            for line in count_lines:
                if not _is_blob_line(repo, line):
                    print(line)
            return code

        inspection_code, inspection_lines = _run_projected(
            _inspection_projection(projected)
        )
        if inspection_code:
            raise RuntimeError("blob:none count inspection traversal failed")

        repo = _promisor._find_repo()
        _render_adjusted_count(repo, count_lines, inspection_lines)
        return code

    code, lines = _run_projected(projected)
    repo = _promisor._find_repo()
    for line in lines:
        if not _is_blob_line(repo, line):
            print(line)
    return code
