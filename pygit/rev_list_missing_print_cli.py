"""Missing-object presentation adapters for promisor-backed ``rev-list``.

The richer ``print-info`` path already owns metadata-only revision/object
selection, boundary framing, counting, and SHA-domain separation. Plain
``print`` reuses that implementation and removes only containing-object
metadata from ``?`` records. Phase242 also composes the same traversal with
``--objects-edge`` by projecting the object mode to ``--objects`` and
prepending Phase234's metadata-only excluded-edge commit records.
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


def _run_objects_edge(argv: Sequence[str], *, plain: bool) -> int:
    """Compose missing-object rendering with Phase234 excluded-edge framing."""

    missing_name = "print" if plain else "print-info"
    if "--boundary" in argv:
        raise ValueError(
            f"--boundary with --objects-edge is not yet supported with --missing={missing_name}"
        )

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
    code, lines = _run_print_info_captured(projected)

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
    ``--objects-edge`` framing without duplicating inventory traversal. Edge
    commits stay local SHA-256 records and remain outside the final ``--count``
    value. ``--boundary + --objects-edge`` remains deliberately deferred.
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
