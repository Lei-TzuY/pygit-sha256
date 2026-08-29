"""Metadata-only ``rev-list --filter=blob:none`` adapter.

The promisor-aware rev-list paths already know how to traverse partial-clone
metadata without materializing promised blobs. This adapter composes Git's
``blob:none`` object filter with those paths instead of falling back to the
historical object walker, where touching a foreign tree entry could trigger a
lazy fetch.

Phase246 introduced line-oriented filtering for the missing-object traversal.
Phase247 extends the same projection to ``--count`` by mirroring the established
Phase240/243 structured count formula on the Phase232 object inventory, while
excluding blobs before counting. Phase248 composes the same filter with the
Phase244/245 NUL object-record protocol by filtering inventory entries before
NUL presentation instead of parsing emitted bytes.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from typing import Optional, Sequence

from . import rev_list_missing_print_cli as _missing_print
from . import rev_list_nul_cli as _nul
from . import rev_list_promisor_cli as _promisor
from .objects import BlobObject
from .promisor import promised_kind


_SUPPORTED_MISSING = {
    "--missing=allow-promisor",
    "--missing=print",
    "--missing=print-info",
}


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
    missing = [arg for arg in projected if arg.startswith("--missing=")]

    # NUL framing already has a structured ordinary-repository mode, so an
    # explicit missing policy is optional there. If supplied, keep the same
    # three metadata-only promisor modes supported by the line-oriented path.
    if "-z" in projected:
        if len(missing) > 1:
            raise ValueError("rev-list accepts exactly one --missing action")
        if missing and missing[0] not in _SUPPORTED_MISSING:
            raise ValueError(
                "--filter=blob:none with -z supports --missing=allow-promisor, print, or print-info"
            )
        return projected

    if len(missing) != 1 or missing[0] not in _SUPPORTED_MISSING:
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


def _parse_inventory_request(argv: Sequence[str]):
    """Parse count selection through the core ``--objects`` missing adapter.

    Plain ``print`` is projected to ``print-info`` because both modes share the
    same traversal, and ``--objects-edge`` is projected to ``--objects`` because
    edge records are a separate presentation channel. Negative revisions remain
    unchanged, so inventory exclusion closure stays authoritative.
    """

    parse_argv: list[str] = []
    for arg in argv:
        if arg == "--objects-edge":
            parse_argv.append("--objects")
        elif arg == "--missing=print":
            parse_argv.append("--missing=print-info")
        else:
            parse_argv.append(arg)

    parsed = _promisor._parse_allow_promisor(parse_argv)
    if parsed is None:
        raise RuntimeError("promisor parser declined blob:none count projection")
    return parsed


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


def _filtered_present_count(repo, argv: Sequence[str]) -> int:
    """Return the Git-style present-object count after applying ``blob:none``."""

    parsed = _parse_inventory_request(argv)

    boundary_commits = ()
    snapshot_commits = None
    if parsed["boundary"]:
        boundary_commits = _promisor._promisor_boundary_commits(
            repo,
            parsed["revisions"],
            all_refs=parsed["all_refs"],
            first_parent=parsed["first_parent"],
            topo_order=parsed["topo_order"],
            reverse=parsed["reverse"],
            skip=parsed["skip"],
            max_count=parsed["max_count"],
        )
        snapshot_commits = tuple(oid for oid, _is_boundary in boundary_commits)

    entries = _promisor.promisor_object_inventory(
        repo,
        parsed["revisions"],
        all_refs=parsed["all_refs"],
        first_parent=parsed["first_parent"],
        topo_order=parsed["topo_order"],
        reverse=parsed["reverse"],
        skip=parsed["skip"],
        max_count=parsed["max_count"],
        snapshot_commits=snapshot_commits,
    )

    if not parsed["boundary"]:
        return sum(
            1
            for entry in entries
            if not entry.missing and entry.type_name != "blob"
        )

    # Boundary presentation owns the selected/boundary commit records. The
    # inventory still contains top-level selected commit entries, so count only
    # present non-blob snapshot objects below that presentation layer. Path-
    # bearing commit objects (gitlinks) remain legitimate snapshot objects.
    snapshot_present = sum(
        1
        for entry in entries
        if not entry.missing
        and entry.type_name != "blob"
        and not (entry.type_name == "commit" and entry.path is None)
    )

    overlap = frozenset()
    if "--objects-edge" in argv:
        edges = _promisor._promisor_object_edges(
            repo,
            parsed["revisions"],
            all_refs=parsed["all_refs"],
            first_parent=parsed["first_parent"],
        )
        overlap = _missing_print._edge_boundary_overlap(repo, parsed, edges)

    return len(boundary_commits) - len(overlap) + snapshot_present


def _render_filtered_count(repo, argv: Sequence[str], lines: Sequence[str]) -> None:
    """Preserve count framing while replacing its integer with filtered count."""

    if not lines:
        raise RuntimeError("rev-list count projection produced no output")
    try:
        int(lines[-1])
    except ValueError as exc:
        raise RuntimeError("rev-list count projection did not end with an integer") from exc

    # Existing count adapters already own object-edge ordering, edge/boundary
    # deduplication, and print-family missing framing. Keep those records, except
    # for promised blobs which blob:none removes from the output entirely.
    for line in lines[:-1]:
        if not _is_blob_line(repo, line):
            print(line)
    print(_filtered_present_count(repo, argv))


def try_run_rev_list_filter(argv: Sequence[str]) -> Optional[int]:
    """Handle ``--filter=blob:none`` for metadata-only object traversal."""

    if _filter_spec(argv) is None:
        return None

    projected = _project(argv)

    if "-z" in projected:
        code = _nul.try_run_rev_list_nul(projected, omit_blobs=True)
        if code is None:
            raise RuntimeError("NUL rev-list adapter declined blob:none projection")
        return code

    count = "--count" in projected
    code, lines = _run_projected(projected)
    repo = _promisor._find_repo()

    if code:
        for line in lines:
            if not _is_blob_line(repo, line):
                print(line)
        return code

    if count:
        _render_filtered_count(repo, projected, lines)
        return code

    for line in lines:
        if not _is_blob_line(repo, line):
            print(line)
    return code
