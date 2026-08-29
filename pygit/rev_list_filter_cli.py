"""Metadata-only ``rev-list --filter`` adapters.

The promisor-aware rev-list paths already know how to traverse partial-clone
metadata without materializing promised blobs. These adapters compose Git object
filters with that traversal instead of falling back to the historical object
walker, where touching a foreign tree entry could trigger a lazy fetch.

Phase246 introduced ``blob:none`` line filtering, Phase247 added structured
counting, and Phase248 composed the filter with NUL records. Phase249 adds the
line-oriented ``object:type=(commit|tree|blob)`` filter. Selected commits and
explicit object edges retain Git's provided/presentation exemptions, while
ordinary snapshot objects, boundary commits, and promised objects are filtered
by their known type without materialization.
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
_SUPPORTED_OBJECT_TYPES = {"commit", "tree", "blob"}


def _filter_spec(argv: Sequence[str]) -> Optional[str]:
    filters = [arg for arg in argv if arg.startswith("--filter=")]
    if not filters:
        return None
    if len(filters) != 1:
        raise ValueError("rev-list accepts exactly one --filter action in this phase")
    spec = filters[0].split("=", 1)[1]
    if spec == "blob:none":
        return spec
    if spec.startswith("object:type="):
        requested = spec.split("=", 1)[1]
        if requested in _SUPPORTED_OBJECT_TYPES:
            return spec
        if requested == "tag":
            raise ValueError(
                "--filter=object:type=tag is not yet supported; annotated-tag traversal is not modelled"
            )
    raise ValueError(
        "pygit currently supports --filter=blob:none and object:type=commit|tree|blob"
    )


def _project(argv: Sequence[str]) -> list[str]:
    """Project ``blob:none`` onto an already-supported underlying traversal."""
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


def _project_object_type(argv: Sequence[str]) -> list[str]:
    """Project one line-oriented ``object:type`` request onto missing traversal."""
    projected = [arg for arg in argv if not arg.startswith("--filter=")]
    if "-z" in projected:
        raise ValueError("--filter=object:type with -z is not yet supported")
    if "--count" in projected:
        raise ValueError("--filter=object:type with --count is not yet supported")
    missing = [arg for arg in projected if arg.startswith("--missing=")]
    if len(missing) != 1 or missing[0] not in _SUPPORTED_MISSING:
        raise ValueError(
            "--filter=object:type currently requires --missing=allow-promisor, print, or print-info"
        )
    return projected


def _run_projected(argv: Sequence[str]) -> tuple[int, tuple[str, ...]]:
    capture = io.StringIO()
    with redirect_stdout(capture):
        code = _missing_print.try_run_rev_list_missing_print(argv)
        if code is None:
            code = _promisor.try_run_rev_list_allow_promisor(argv)
    if code is None:
        raise RuntimeError("promisor rev-list adapter declined filter projection")
    return code, tuple(capture.getvalue().splitlines())


def _parse_inventory_request(argv: Sequence[str]):
    """Parse selection through the core ``--objects`` missing adapter.

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
        raise RuntimeError("promisor parser declined filter projection")
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


def _line_oid(line: str) -> tuple[str, str]:
    """Return (prefix, oid) for one line-oriented object record."""
    token = line.split(None, 1)[0] if line else ""
    prefix = token[:1] if token[:1] in {"?", "-"} else ""
    oid = token[1:] if prefix else token
    return prefix, oid.lower()


def _local_type(repo, oid: str) -> Optional[str]:
    if len(oid) != 64 or any(ch not in "0123456789abcdef" for ch in oid):
        return None
    try:
        obj = repo.store.read(oid)
    except (FileNotFoundError, KeyError):
        return None
    value = getattr(obj, "type_name", None)
    if not isinstance(value, (bytes, bytearray)):
        return None
    return bytes(value).decode("ascii")


def _object_type_context(repo, argv: Sequence[str]):
    """Return parsed selection plus Git-style selected/edge exemptions."""
    parsed = _parse_inventory_request(argv)
    selected = {
        entry.oid.lower()
        for entry in _promisor.rev_list(
            repo,
            parsed["revisions"],
            all_refs=parsed["all_refs"],
            first_parent=parsed["first_parent"],
            topo_order=parsed["topo_order"],
            reverse=parsed["reverse"],
            skip=parsed["skip"],
            max_count=parsed["max_count"],
            left_right=False,
        )
    }
    edges = frozenset()
    if "--objects-edge" in argv:
        edges = frozenset(
            _promisor._promisor_object_edges(
                repo,
                parsed["revisions"],
                all_refs=parsed["all_refs"],
                first_parent=parsed["first_parent"],
            )
        )
    return parsed, selected, edges


def _keep_object_type_line(
    repo,
    line: str,
    *,
    requested: str,
    selected: set[str],
    edges: frozenset[str],
) -> bool:
    """Apply Git's object:type filter without hiding explicit commit framing."""
    if not line:
        return False
    prefix, oid = _line_oid(line)

    # Commits selected by the revision walk are explicit traversal records and
    # remain visible for tree/blob filters. Likewise --objects-edge advertises
    # explicit exclusion edges independently of the object filter.
    if prefix == "" and oid in selected:
        return True
    if prefix == "-" and oid in edges:
        return True

    if prefix == "?":
        kind = promised_kind(repo.pygit_dir, oid)
        if kind is None:
            raise RuntimeError(f"missing object {oid} has no promisor type metadata")
        return kind == requested

    kind = _local_type(repo, oid)
    if kind is None:
        # Preserve malformed/unrecognised records instead of silently masking
        # integrity problems behind a filter decision we cannot make.
        return True
    return kind == requested


def _run_object_type_filter(argv: Sequence[str], *, requested: str) -> int:
    projected = _project_object_type(argv)
    code, lines = _run_projected(projected)
    repo = _promisor._find_repo()
    _parsed, selected, edges = _object_type_context(repo, projected)
    for line in lines:
        if _keep_object_type_line(
            repo,
            line,
            requested=requested,
            selected=selected,
            edges=edges,
        ):
            print(line)
    return code


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

    for line in lines[:-1]:
        if not _is_blob_line(repo, line):
            print(line)
    print(_filtered_present_count(repo, argv))


def try_run_rev_list_filter(argv: Sequence[str]) -> Optional[int]:
    """Handle supported metadata-only ``rev-list --filter`` modes."""

    spec = _filter_spec(argv)
    if spec is None:
        return None

    if spec.startswith("object:type="):
        return _run_object_type_filter(argv, requested=spec.split("=", 1)[1])

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
