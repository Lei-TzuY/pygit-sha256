"""Promisor-aware ``rev-list --objects --missing=allow-promisor`` adapter.

Git's allow-promisor mode keeps object traversal local: expected missing
promisor objects are silently omitted while present objects are still printed.
pygit additionally has to preserve its SHA-256-native object domain, so an
unresolved foreign SHA-1 promise must never be rendered as if it were a local
SHA-256 object id.
"""

from __future__ import annotations

from typing import Optional, Sequence

from .entrypoint import _find_repo
from .promisor_object_inventory import promisor_object_inventory


_VALUE_OPTIONS = {"--skip", "--max-count", "-n"}
_UNSUPPORTED = {
    "--objects-edge",
    "--boundary",
    "--parents",
    "--children",
    "--left-right",
    "--left-only",
    "--right-only",
    "--max-count-oldest",
    "--min-parents",
    "--max-parents",
    "--merges",
    "--no-merges",
    "--max-age",
    "--min-age",
    "--disk-usage",
    "--header",
    "--timestamp",
}


def _int_value(option: str, raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{option} requires an integer") from exc
    if value < 0:
        raise ValueError(f"{option} must be non-negative")
    return value


def _parse_allow_promisor(argv: Sequence[str]):
    """Parse the inventory-backed subset, returning ``None`` when unused."""

    missing = [arg for arg in argv if arg.startswith("--missing=")]
    if not missing:
        return None
    if len(missing) != 1 or missing[0] != "--missing=allow-promisor":
        raise ValueError("pygit currently supports only --missing=allow-promisor")
    if "--objects" not in argv:
        raise ValueError("--missing=allow-promisor currently requires --objects")

    all_refs = False
    first_parent = False
    topo_order = False
    reverse = False
    no_object_names = False
    count = False
    skip = 0
    max_count = 0
    revisions: list[str] = []

    args = list(argv)
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"--objects", "--missing=allow-promisor"}:
            index += 1
            continue
        if arg == "--all":
            all_refs = True
            index += 1
            continue
        if arg == "--first-parent":
            first_parent = True
            index += 1
            continue
        if arg == "--topo-order":
            topo_order = True
            index += 1
            continue
        if arg == "--reverse":
            reverse = True
            index += 1
            continue
        if arg == "--no-object-names":
            no_object_names = True
            index += 1
            continue
        if arg == "--count":
            count = True
            index += 1
            continue
        if arg.startswith("--skip="):
            skip = _int_value("--skip", arg.split("=", 1)[1])
            index += 1
            continue
        if arg.startswith("--max-count="):
            max_count = _int_value("--max-count", arg.split("=", 1)[1])
            index += 1
            continue
        if arg.startswith("-n") and arg != "-n":
            max_count = _int_value("-n", arg[2:])
            index += 1
            continue
        if arg in _VALUE_OPTIONS:
            if index + 1 >= len(args):
                raise ValueError(f"{arg} requires a value")
            value = _int_value(arg, args[index + 1])
            if arg == "--skip":
                skip = value
            else:
                max_count = value
            index += 2
            continue
        if arg in _UNSUPPORTED or any(arg.startswith(option + "=") for option in _UNSUPPORTED):
            raise ValueError(f"{arg} is not yet supported with --missing=allow-promisor")
        if arg.startswith("--"):
            raise ValueError(f"unsupported rev-list option with --missing=allow-promisor: {arg}")
        revisions.append(arg)
        index += 1

    return {
        "all_refs": all_refs,
        "first_parent": first_parent,
        "topo_order": topo_order,
        "reverse": reverse,
        "no_object_names": no_object_names,
        "count": count,
        "skip": skip,
        "max_count": max_count,
        "revisions": tuple(revisions),
    }


def try_run_rev_list_allow_promisor(argv: Sequence[str]) -> Optional[int]:
    """Handle ``--missing=allow-promisor`` or return ``None`` to delegate.

    The Phase232 inventory distinguishes present SHA-256 objects from unresolved
    native SHA-1 promises without materializing either.  Git's allow-promisor
    mode silently omits expected missing objects, so only entries with a real
    repository-visible ``oid`` are rendered here.
    """

    parsed = _parse_allow_promisor(argv)
    if parsed is None:
        return None

    repo = _find_repo()
    entries = promisor_object_inventory(
        repo,
        parsed["revisions"],
        all_refs=parsed["all_refs"],
        first_parent=parsed["first_parent"],
        topo_order=parsed["topo_order"],
        reverse=parsed["reverse"],
        skip=parsed["skip"],
        max_count=parsed["max_count"],
    )
    present = [entry for entry in entries if entry.oid is not None]

    if parsed["count"]:
        print(len(present))
        return 0

    for entry in present:
        assert entry.oid is not None
        if parsed["no_object_names"] or entry.path is None:
            print(entry.oid)
        else:
            print(f"{entry.oid} {entry.path}")
    return 0
