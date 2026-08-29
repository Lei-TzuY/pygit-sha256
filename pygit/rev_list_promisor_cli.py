"""Promisor-aware ``rev-list --objects* --missing=...`` adapter.

The metadata-only inventory keeps pygit's repository-visible SHA-256 object
identity separate from unresolved foreign Git SHA-1 promisor identities.
``allow-promisor`` silently omits expected promises.  ``print-info`` exposes
those omissions through Git's ``?`` missing-object channel, where the OID is
explicitly the native transport identity rather than a local repository OID.
"""

from __future__ import annotations

import json
from typing import Optional, Sequence, Tuple

from .entrypoint import _find_repo
from .objects import CommitObject
from .promisor_object_inventory import (
    PromisorObjectInventoryEntry,
    promisor_object_inventory,
)
from .repo import Repository
from .rev_list import _object_exclusion_roots, _shallow_boundaries, _walk, rev_list
from .rev_list_boundary import rev_list_boundary


_VALUE_OPTIONS = {"--skip", "--max-count", "-n"}
_UNSUPPORTED = {
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
_SUPPORTED_MISSING = {"allow-promisor", "print-info"}


def _int_value(option: str, raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{option} requires an integer") from exc
    if value < 0:
        raise ValueError(f"{option} must be non-negative")
    return value


def _missing_action(argv: Sequence[str]) -> Optional[str]:
    missing = [arg for arg in argv if arg.startswith("--missing=")]
    if not missing:
        return None
    if len(missing) != 1:
        raise ValueError("rev-list accepts exactly one --missing action")
    action = missing[0].split("=", 1)[1]
    if action not in _SUPPORTED_MISSING:
        supported = ", ".join(sorted(_SUPPORTED_MISSING))
        raise ValueError(f"pygit currently supports --missing={{{supported}}}")
    return action


def _parse_allow_promisor(argv: Sequence[str]):
    """Parse the inventory-backed missing-object subset."""

    missing_action = _missing_action(argv)
    if missing_action is None:
        return None

    object_modes = [arg for arg in argv if arg in {"--objects", "--objects-edge"}]
    if len(object_modes) != 1:
        raise ValueError(
            f"--missing={missing_action} requires exactly one of --objects or --objects-edge"
        )
    objects_edge = object_modes[0] == "--objects-edge"

    all_refs = False
    first_parent = False
    topo_order = False
    reverse = False
    no_object_names = False
    count = False
    boundary = False
    skip = 0
    max_count = 0
    revisions: list[str] = []

    args = list(argv)
    index = 0
    missing_option = f"--missing={missing_action}"
    while index < len(args):
        arg = args[index]
        if arg in {"--objects", "--objects-edge", missing_option}:
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
        if arg == "--boundary":
            boundary = True
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
            raise ValueError(f"{arg} is not yet supported with --missing={missing_action}")
        if arg.startswith("--"):
            raise ValueError(
                f"unsupported rev-list option with --missing={missing_action}: {arg}"
            )
        revisions.append(arg)
        index += 1

    if boundary and objects_edge:
        raise ValueError(
            f"--boundary with --objects-edge is not yet supported with --missing={missing_action}"
        )
    if missing_action == "print-info":
        if objects_edge:
            raise ValueError(
                "--objects-edge is not yet supported with --missing=print-info"
            )
        if boundary:
            raise ValueError("--boundary is not yet supported with --missing=print-info")
        if count:
            raise ValueError("--count is not yet supported with --missing=print-info")

    return {
        "missing_action": missing_action,
        "all_refs": all_refs,
        "first_parent": first_parent,
        "topo_order": topo_order,
        "reverse": reverse,
        "no_object_names": no_object_names,
        "objects_edge": objects_edge,
        "boundary": boundary,
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
    """Return Git-style excluded edge commits without reading promised blobs."""

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

    return tuple(sorted(edges))


def _promisor_boundary_commits(
    repo: Repository,
    revisions: Sequence[str],
    *,
    all_refs: bool,
    first_parent: bool,
    topo_order: bool,
    reverse: bool,
    skip: int,
    max_count: int,
) -> Tuple[Tuple[str, bool], ...]:
    """Return selected/boundary commit framing using commit metadata only."""

    entries = rev_list_boundary(
        repo,
        revisions,
        all_refs=all_refs,
        first_parent=first_parent,
        topo_order=topo_order,
        reverse=reverse,
        skip=skip,
        max_count=max_count,
        side_mode=False,
        left_only=False,
        right_only=False,
    )
    return tuple((entry.oid.lower(), entry.boundary) for entry in entries)


def _print_info_path(path: str) -> str:
    """Encode a print-info path without leaving an ambiguous bare token."""
    if path and all(
        0x21 <= ord(ch) < 0x7F and ch not in {'"', '\\'}
        for ch in path
    ):
        return path
    return json.dumps(path, ensure_ascii=False)


def _missing_print_info(entry: PromisorObjectInventoryEntry) -> str:
    """Render one unresolved promise through the missing/native identity channel."""
    if entry.native_oid is None:
        raise RuntimeError("missing inventory entry has no native object identity")
    parts = [f"?{entry.native_oid.lower()}"]
    if entry.path is not None:
        parts.append(f"path={_print_info_path(entry.path)}")
    parts.append(f"type={entry.type_name}")
    return " ".join(parts)


def _print_present(entry: PromisorObjectInventoryEntry, *, no_object_names: bool) -> None:
    if entry.oid is None:
        raise RuntimeError("present inventory entry has no local SHA-256 identity")
    if no_object_names or entry.path is None:
        print(entry.oid)
    else:
        print(f"{entry.oid} {entry.path}")


def try_run_rev_list_allow_promisor(argv: Sequence[str]) -> Optional[int]:
    """Handle inventory-backed ``--missing`` modes without materialization.

    Unprefixed present-object lines always carry repository-visible SHA-256 ids.
    Under ``print-info`` only, ``?`` lines are explicitly missing/native
    transport identities and may therefore carry upstream SHA-1 ids together
    with containing-tree path/type metadata.  No surrogate SHA-256 is invented.
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

    boundary_commits: Tuple[Tuple[str, bool], ...] = ()
    snapshot_commits = None
    if parsed["boundary"]:
        boundary_commits = _promisor_boundary_commits(
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

    entries = promisor_object_inventory(
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

    if parsed["missing_action"] == "print-info":
        for entry in entries:
            if entry.missing:
                print(_missing_print_info(entry))
            else:
                _print_present(entry, no_object_names=parsed["no_object_names"])
        return 0

    present = [entry for entry in entries if entry.oid is not None]

    for oid in edges:
        print(f"-{oid}")

    if parsed["boundary"]:
        non_commits = [entry for entry in present if entry.type_name != "commit"]
        if parsed["count"]:
            print(len(boundary_commits) + len(non_commits))
            return 0
        for oid, is_boundary in boundary_commits:
            print(f"-{oid}" if is_boundary else oid)
        for entry in non_commits:
            _print_present(entry, no_object_names=parsed["no_object_names"])
        return 0

    if parsed["count"]:
        print(len(present))
        return 0

    for entry in present:
        _print_present(entry, no_object_names=parsed["no_object_names"])
    return 0
