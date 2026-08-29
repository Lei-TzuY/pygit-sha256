"""Git-style ``rev-list --objects* --in-commit-order`` traversal.

The existing promisor object inventory deliberately emits all selected commits
before walking their snapshots. Git's ``--in-commit-order`` mode changes only
that presentation order: each selected commit is emitted immediately before the
first tree/blob objects reached from that commit, while object identity remains
globally deduplicated across the walk.

Phase260 composes that ordering with ``--boundary``. Phase261 additionally
composes it with ``--objects-edge``: excluded edge commits are emitted first,
then selected commits and their first-seen snapshot objects remain interleaved.
Explicit negative-revision closure still subtracts snapshot objects, but never
removes top-level selected/boundary presentation frames.
"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

from . import promisor_object_inventory as _inventory
from . import rev_list_promisor_cli as _promisor
from .objects import CommitObject
from .promisor_object_inventory import PromisorObjectInventoryEntry
from .rev_list import _object_exclusion_roots, rev_list


_IN_COMMIT_ORDER = "--in-commit-order"
_SUPPORTED_MISSING = {"allow-promisor", "print", "print-info"}


def _parse(argv: Sequence[str]):
    if _IN_COMMIT_ORDER not in argv:
        return None

    if "-z" in argv:
        raise ValueError("rev-list --in-commit-order with -z is not yet supported")
    if any(arg == "--disk-usage" or arg.startswith("--disk-usage=") for arg in argv):
        raise ValueError("rev-list --in-commit-order with --disk-usage is not yet supported")
    if any(
        arg.startswith("--filter=")
        or arg in {"--filter-print-omitted", "--filter-provided-objects"}
        for arg in argv
    ):
        raise ValueError("rev-list --in-commit-order with --filter is not yet supported")

    object_modes = [arg for arg in argv if arg in {"--objects", "--objects-edge"}]
    if len(object_modes) != 1:
        raise ValueError(
            "rev-list --in-commit-order currently requires exactly one of --objects or --objects-edge"
        )
    objects_edge = object_modes[0] == "--objects-edge"
    if objects_edge and "--boundary" in argv:
        raise ValueError(
            "rev-list --in-commit-order with --objects-edge and --boundary is not yet supported"
        )

    missing = [arg for arg in argv if arg.startswith("--missing=")]
    if len(missing) > 1:
        raise ValueError("rev-list accepts exactly one --missing action")

    if missing:
        mode = missing[0].split("=", 1)[1]
        if mode not in _SUPPORTED_MISSING:
            supported = ", ".join(sorted(_SUPPORTED_MISSING))
            raise ValueError(
                f"rev-list --in-commit-order supports --missing={{{supported}}}"
            )
    else:
        mode = "ordinary"

    # Reuse the mature inventory parser with an --objects projection, then keep
    # objects-edge presentation local to this adapter. This avoids inheriting
    # older generic parser guards for print-info + objects-edge while preserving
    # exactly the same revision-selection controls.
    projected: list[str] = []
    for arg in argv:
        if arg == _IN_COMMIT_ORDER:
            continue
        if arg == "--objects-edge":
            projected.append("--objects")
        elif arg == "--missing=print":
            projected.append("--missing=print-info")
        else:
            projected.append(arg)
    if mode == "ordinary":
        projected.append("--missing=allow-promisor")

    parsed = _promisor._parse_allow_promisor(projected)
    if parsed is None:
        raise RuntimeError("promisor rev-list parser declined in-commit-order projection")
    parsed = dict(parsed)
    parsed["in_commit_order_missing_mode"] = mode
    parsed["in_commit_order_objects_edge"] = objects_edge
    return parsed


def _commit_frames(repo, parsed) -> Tuple[Tuple[str, bool], ...]:
    """Return selected/boundary commit frames in final presentation order."""

    if parsed["boundary"]:
        return _promisor._promisor_boundary_commits(
            repo,
            parsed["revisions"],
            all_refs=parsed["all_refs"],
            first_parent=parsed["first_parent"],
            topo_order=parsed["topo_order"],
            reverse=parsed["reverse"],
            skip=parsed["skip"],
            max_count=parsed["max_count"],
        )

    commits = rev_list(
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
    return tuple((entry.oid.lower(), False) for entry in commits)


def _ordered_inventory(
    repo, parsed
) -> Tuple[Tuple[PromisorObjectInventoryEntry, ...], frozenset[str]]:
    """Build commit/snapshot-interleaved inventory with global object dedupe.

    Boundary commit frames are presentation records and therefore survive
    explicit negative-revision closure subtraction. Snapshot objects, including
    path-bearing gitlink commits, remain subject to the normal exclusion closure.
    """

    frames = _commit_frames(repo, parsed)
    if not frames:
        return (), frozenset()

    output: list[PromisorObjectInventoryEntry] = []
    seen: set[tuple[str, str]] = set()
    framed_oids: set[str] = set()
    boundary_oids: set[str] = set()

    for oid, is_boundary in frames:
        oid = oid.lower()
        obj = repo.store.read(oid)
        if not isinstance(obj, CommitObject):
            raise RuntimeError(f"Object {oid} in rev-list traversal is not a commit")
        framed_oids.add(oid)
        if is_boundary:
            boundary_oids.add(oid)
        _inventory._append_unique(
            output,
            seen,
            PromisorObjectInventoryEntry(type_name="commit", oid=oid),
        )
        _inventory._walk_tree(
            repo,
            obj.tree.lower(),
            "",
            output=output,
            seen=seen,
            active=set(),
        )

    exclusion_roots = _object_exclusion_roots(
        repo,
        parsed["revisions"],
        first_parent=parsed["first_parent"],
    )
    if exclusion_roots:
        excluded = {
            _inventory._key(entry)
            for entry in _inventory._walk_commit_closure(
                repo,
                exclusion_roots,
                first_parent=parsed["first_parent"],
            )
        }
        output = [
            entry
            for entry in output
            if (
                entry.type_name == "commit"
                and entry.path is None
                and entry.oid is not None
                and entry.oid.lower() in framed_oids
            )
            or _inventory._key(entry) not in excluded
        ]

    return tuple(output), frozenset(boundary_oids)


def _plain_missing(entry: PromisorObjectInventoryEntry) -> str:
    if entry.native_oid is None:
        raise RuntimeError("missing inventory entry has no native object identity")
    return f"?{entry.native_oid.lower()}"


def _print_present_ordered(
    entry: PromisorObjectInventoryEntry,
    *,
    parsed,
    boundary_oids: frozenset[str],
) -> None:
    if entry.oid is None:
        raise RuntimeError("present inventory entry has no local SHA-256 identity")
    oid = entry.oid.lower()
    if entry.type_name == "commit" and entry.path is None and oid in boundary_oids:
        print(f"-{oid}")
        return
    _promisor._print_present(entry, no_object_names=parsed["no_object_names"])


def _render(
    entries: Sequence[PromisorObjectInventoryEntry],
    *,
    parsed,
    mode: str,
    boundary_oids: frozenset[str],
    edges: Sequence[str] = (),
) -> int:
    missing = tuple(entry for entry in entries if entry.missing)
    if mode == "ordinary" and missing:
        native = missing[0].native_oid or "unknown"
        raise RuntimeError(
            f"missing object {native}; use --missing=allow-promisor, print, or print-info"
        )

    # Native Git emits excluded object edges before the in-commit-order object
    # stream, even with --reverse and --count. Edge records are presentation
    # metadata and do not contribute to the present-object count.
    for oid in edges:
        print(f"-{oid}")

    present_count = 0
    for entry in entries:
        if entry.missing:
            if mode == "print-info":
                print(_promisor._missing_print_info(entry))
            elif mode == "print":
                print(_plain_missing(entry))
            continue

        present_count += 1
        if not parsed["count"]:
            _print_present_ordered(
                entry,
                parsed=parsed,
                boundary_oids=boundary_oids,
            )

    if parsed["count"]:
        print(present_count)
    return 0


def try_run_rev_list_in_commit_order(argv: Sequence[str]) -> Optional[int]:
    """Handle ordered object traversal without materializing promises."""

    parsed = _parse(argv)
    if parsed is None:
        return None

    repo = _promisor._find_repo()
    entries, boundary_oids = _ordered_inventory(repo, parsed)
    edges: Tuple[str, ...] = ()
    if parsed["in_commit_order_objects_edge"]:
        edges = _promisor._promisor_object_edges(
            repo,
            parsed["revisions"],
            all_refs=parsed["all_refs"],
            first_parent=parsed["first_parent"],
        )
    return _render(
        entries,
        parsed=parsed,
        mode=parsed["in_commit_order_missing_mode"],
        boundary_oids=boundary_oids,
        edges=edges,
    )
