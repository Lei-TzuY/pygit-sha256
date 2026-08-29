"""Missing-object presentation adapters for promisor-backed ``rev-list``.

The richer ``print-info`` path already owns metadata-only revision/object
selection, boundary framing, counting, and SHA-domain separation. Plain
``print`` reuses that implementation and removes only containing-object
metadata from ``?`` records. Phase242 also composes the same traversal with
``--objects-edge`` by projecting the object mode to ``--objects`` and
prepending Phase234's metadata-only excluded-edge commit records.

Phase243 completes that composition for ``--boundary``. Git treats an explicit
excluded commit which is both an object edge and a boundary as one leading
``-<oid>`` record, not two records. The adapter therefore removes the duplicate
boundary presentation (or its contribution to ``--count``) after projecting to
the already-tested ``--objects --boundary`` traversal.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from typing import Optional, Sequence

from . import rev_list_promisor_cli as _promisor


def _print_missing_mode(argv: Sequence[str]) -> Optional[str]:
    """Return ``print``/``print-info`` when this adapter owns the request."""

    missing = [arg for arg in argv if arg.startswith("--missing=")]
    owned = [arg for arg in missing if arg in {"--missing=print", "--missing=print-info"}]
    if not owned:
        return None
    if len(missing) != 1:
        raise ValueError("rev-list accepts exactly one --missing action")
    return owned[0].split("=", 1)[1]


def _to_print_info(argv: Sequence[str]) -> list[str]:
    converted: list[str] = []
    seen = 0
    for arg in argv:
        if arg == "--missing=print":
            converted.append("--missing=print-info")
            seen += 1
        else:
            converted.append(arg)
    if seen != 1:
        raise ValueError("rev-list accepts exactly one --missing action")
    return converted


def _objects_projection(argv: Sequence[str], *, plain: bool) -> list[str]:
    """Project ``--objects-edge`` onto the richer ``--objects`` traversal."""

    projected: list[str] = []
    seen_edge = 0
    for arg in argv:
        if arg == "--objects-edge":
            projected.append("--objects")
            seen_edge += 1
        elif plain and arg == "--missing=print":
            projected.append("--missing=print-info")
        else:
            projected.append(arg)
    if seen_edge != 1:
        raise ValueError("--objects-edge projection requires exactly one object mode")
    return projected


def _strip_print_info(line: str) -> str:
    """Collapse ``?<oid> token=value...`` to Git's plain ``?<oid>`` form."""

    if not line.startswith("?"):
        return line
    return line.split(None, 1)[0]


def _run_print_info_captured(argv: Sequence[str]) -> tuple[int, tuple[str, ...]]:
    capture = io.StringIO()
    with redirect_stdout(capture):
        code = _promisor.try_run_rev_list_allow_promisor(argv)
    if code is None:
        raise RuntimeError("print-info promisor adapter declined missing-object projection")
    return code, tuple(capture.getvalue().splitlines())


def _edge_boundary_overlap(repo, parsed, edges: Sequence[str]) -> frozenset[str]:
    """Return explicit object edges also rendered by ``--boundary``.

    Native Git emits such commits once, using the leading object-edge framing.
    Limit-induced boundaries which are not explicit exclusion edges remain in
    the projected boundary stream.
    """

    if not parsed["boundary"] or not edges:
        return frozenset()
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
    boundary_oids = {oid for oid, is_boundary in boundary_commits if is_boundary}
    return frozenset(oid for oid in edges if oid in boundary_oids)


def _dedupe_edge_boundaries(
    lines: Sequence[str],
    *,
    overlap: frozenset[str],
    count: bool,
) -> tuple[str, ...]:
    """Remove duplicate edge/boundary framing from projected output."""

    if not overlap:
        return tuple(lines)

    if count:
        if not lines:
            raise RuntimeError("boundary count projection produced no output")
        tail = lines[-1]
        try:
            value = int(tail)
        except ValueError as exc:
            raise RuntimeError("boundary count projection did not end with a count") from exc
        value -= len(overlap)
        if value < 0:
            raise RuntimeError("boundary/object-edge count overlap exceeded projected count")
        return tuple(lines[:-1]) + (str(value),)

    duplicate_lines = {f"-{oid}" for oid in overlap}
    return tuple(line for line in lines if line not in duplicate_lines)


def _run_objects_edge(argv: Sequence[str], *, plain: bool) -> int:
    """Compose missing-object rendering with Phase234 excluded-edge framing."""

    projected = _objects_projection(argv, plain=plain)
    parsed = _promisor._parse_allow_promisor(projected)
    if parsed is None:
        raise RuntimeError("print-info parser declined objects-edge projection")

    repo = _promisor._find_repo()
    edges = _promisor._promisor_object_edges(
        repo,
        parsed["revisions"],
        all_refs=parsed["all_refs"],
        first_parent=parsed["first_parent"],
    )
    overlap = _edge_boundary_overlap(repo, parsed, edges)
    code, lines = _run_print_info_captured(projected)
    lines = _dedupe_edge_boundaries(lines, overlap=overlap, count=parsed["count"])

    for oid in edges:
        print(f"-{oid}")
    for line in lines:
        print(_strip_print_info(line) if plain else line)
    return code


def try_run_rev_list_missing_print(argv: Sequence[str]) -> Optional[int]:
    """Handle plain ``print`` and print-family ``--objects-edge`` projection.

    Unprefixed present-object records remain repository-visible SHA-256 ids.
    Missing records use the explicit ``?`` channel and retain the native
    transport identity already selected by ``print-info``. No surrogate
    SHA-256 object name is invented.

    Phase242 lets both ``print`` and ``print-info`` inherit Phase234's
    ``--objects-edge`` framing without duplicating inventory traversal. Phase243
    additionally composes ``--boundary``: explicit exclusion edges remain one
    leading ``-<local SHA-256>`` record, while distinct limit-induced boundary
    commits keep their normal boundary framing. Under ``--count`` the duplicate
    edge/boundary record is removed from the projected present-object count.
    """

    mode = _print_missing_mode(argv)
    if mode is None:
        return None

    if "--objects-edge" in argv:
        return _run_objects_edge(argv, plain=mode == "print")

    if mode == "print-info":
        return None

    code, lines = _run_print_info_captured(_to_print_info(argv))
    for line in lines:
        print(_strip_print_info(line))
    return code
