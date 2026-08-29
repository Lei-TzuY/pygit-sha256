"""Promisor-aware ``rev-list --objects* --missing=allow-promisor`` adapter.

Git's allow-promisor mode keeps object traversal local: expected missing
promisor objects are silently omitted while present objects are still printed.
pygit additionally has to preserve its SHA-256-native object domain, so an
unresolved foreign SHA-1 promise must never be rendered as if it were a local
SHA-256 object id.
"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

from .entrypoint import _find_repo
from .objects import CommitObject
from .promisor_object_inventory import promisor_object_inventory
from .repo import Repository
from .rev_list import _object_exclusion_roots, _shallow_boundaries, _walk, rev_list


_VALUE_OPTIONS = {"--skip", "--max-count", "-n"}
_UNSUPPORTED = {
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

    object_modes = [arg for arg in argv if arg in {"--objects", "--objects-edge"}]
    if len(object_modes) != 1:
        raise ValueError(
            "--missing=allow-promisor requires exactly one of --objects or --objects-edge"
        )
    objects_edge = object_modes[0] == "--objects-edge"

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
        if arg in {"--objects", "--objects-edge", "--missing=allow-promisor"}:
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
        "objects_edge": objects_edge,
        "count": count,
        "skip": skip,
        "max_count": max_count,
        "revisions": tuple(revisions),
    }


def _promisor_object_edges(
    repo: Repository,
    revisions: Sequence[str],
    *,
    all_refs: bool,
    first_parent: bool,
) -> Tuple[str, ...]:
    """Return Git-style excluded edge commits without reading promised blobs.

    ``--objects-edge`` reports commits just beyond the interesting revision set,
    prefixed with ``-``.  Limits such as ``--max-count`` affect printed commits,
    but not this revision boundary, so edge discovery deliberately walks the
    unlimited commit selection.  Only commit metadata is read.
    """

    exclusion_roots = _object_exclusion_roots(
        repo,
        revisions,
        first_parent=first_parent,
    )
    if not exclusion_roots:
        return ()

    excluded = _walk(repo, exclusion_roots, first_parent=first_parent)
    if not excluded:
        return ()

    selected = rev_list(
        repo,
        revisions,
        all_refs=all_refs,
        first_parent=first_parent,
        topo_order=False,
        reverse=False,
        skip=0,
        max_count=0,
        left_right=False,
    )
    shallow = _shallow_boundaries(repo)
    edges: set[str] = set()
    for entry in selected:
        oid = entry.oid.lower()
        if oid in shallow:
            continue
        obj = repo.store.read(oid)
        if not isinstance(obj, CommitObject):
            raise RuntimeError(f"Object {oid} in rev-list traversal is not a commit")
        parents = obj.parents[:1] if first_parent else obj.parents
        for parent in parents:
            parent_oid = parent.lower()
            if parent_oid in excluded:
                edges.add(parent_oid)

    # Native ``rev-list --objects-edge`` renders the excluded boundary before
    # the selected commits. OID ordering is deterministic when several edges
    # exist while preserving the exact single-edge representation.
    return tuple(sorted(edges))


def try_run_rev_list_allow_promisor(argv: Sequence[str]) -> Optional[int]:
    """Handle inventory-backed ``--missing=allow-promisor`` modes.

    The Phase232 inventory distinguishes present SHA-256 objects from unresolved
    native SHA-1 promises without materializing either. Git's allow-promisor
    mode silently omits expected missing objects, so only entries with a real
    repository-visible ``oid`` are rendered here. ``--objects-edge`` adds only
    local excluded commit identities and therefore remains metadata-only.
    """

    parsed = _parse_allow_promisor(argv)
    if parsed is None:
        return None

    repo = _find_repo()
    edges: Tuple[str, ...] = ()
    if parsed["objects_edge"]:
        edges = _promisor_object_edges(
            repo,
            parsed["revisions"],
            all_refs=parsed["all_refs"],
            first_parent=parsed["first_parent"],
        )

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

    for oid in edges:
        print(f"-{oid}")

    if parsed["count"]:
        # Native Git prints edge lines separately and does not include them in
        # the following object count.
        print(len(present))
        return 0

    for entry in present:
        assert entry.oid is not None
        if parsed["no_object_names"] or entry.path is None:
            print(entry.oid)
        else:
            print(f"{entry.oid} {entry.path}")
    return 0
