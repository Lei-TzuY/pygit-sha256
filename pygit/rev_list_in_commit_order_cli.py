"""Git-style ``rev-list --objects* --in-commit-order`` traversal.

The existing promisor object inventory deliberately emits all selected commits
before walking their snapshots. Git's ``--in-commit-order`` mode changes only
that presentation order: each selected commit is emitted immediately before the
first tree/blob objects reached from that commit, while object identity remains
globally deduplicated across the walk.

Phase260 composes that ordering with ``--boundary``. Phase261 additionally
composes it with ``--objects-edge``: excluded edge commits are emitted first,
then selected commits and their first-seen snapshot objects remain interleaved.
Phase262 composes all three modes together. If an explicit exclusion is both an
object edge and a boundary frame, the leading edge owns presentation and the
later boundary frame is suppressed; limit-induced boundaries remain in their
commit/snapshot position. Phase263 adds Git's structured ``-z`` object metadata
protocol for the ``--objects`` path without changing traversal or SHA domains.
Explicit negative-revision closure still subtracts snapshot objects without
removing unrelated top-level presentation frames.
"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

from . import promisor_object_inventory as _inventory
from . import rev_list_nul_cli as _nul
from . import rev_list_promisor_cli as _promisor
from .objects import CommitObject
from .promisor_object_inventory import PromisorObjectInventoryEntry
from .rev_list import _object_exclusion_roots, rev_list


_IN_COMMIT_ORDER = "--in-commit-order"
_SUPPORTED_MISSING = {"allow-promisor", "print", "print-info"}


def _parse(argv: Sequence[str]):
    if _IN_COMMIT_ORDER not in argv:
        return None

    nul = "-z" in argv
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
    if nul and objects_edge:
        raise ValueError("rev-list -z is only compatible with --objects, --boundary, and --missing")
    if nul and "--count" in argv:
        raise ValueError("rev-list -z is not compatible with --count")

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
    # ordering/presentation local to this adapter. ``-z`` is stripped before
    # projection because it is handled structurally below, not by the generic
    # promisor line renderer.
    projected: list[str] = []
    for arg in argv:
        if arg in {_IN_COMMIT_ORDER, "-z"}:
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
    parsed["in_commit_order_nul"] = nul
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


def _dedupe_edge_boundary_overlap(
    entries: Sequence[PromisorObjectInventoryEntry],
    *,
    boundary_oids: frozenset[str],
    edges: Sequence[str],
) -> Tuple[Tuple[PromisorObjectInventoryEntry, ...], frozenset[str]]:
    """Let a leading object-edge record own any overlapping boundary frame.

    Native Git prints an explicit excluded commit only once when it is both an
    ``--objects-edge`` record and a ``--boundary`` commit. The edge appears at
    the front of the output; the later top-level boundary frame is suppressed.
    Snapshot entries are left untouched so non-overlapping limit boundaries keep
    their normal commit/snapshot interleaving.
    """

    overlap = frozenset(oid.lower() for oid in edges) & boundary_oids
    if not overlap:
        return tuple(entries), boundary_oids

    filtered = tuple(
        entry
        for entry in entries
        if not (
            entry.type_name == "commit"
            and entry.path is None
            and entry.oid is not None
            and entry.oid.lower() in overlap
        )
    )
    return filtered, frozenset(oid for oid in boundary_oids if oid not in overlap)


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


def _render_nul(
    entries: Sequence[PromisorObjectInventoryEntry],
    *,
    parsed,
    mode: str,
    boundary_oids: frozenset[str],
) -> int:
    """Render ordered inventory using Git's structured NUL object protocol."""

    missing = tuple(entry for entry in entries if entry.missing)
    if mode == "ordinary" and missing:
        native = missing[0].native_oid or "unknown"
        raise RuntimeError(
            f"missing object {native}; use --missing=allow-promisor, print, or print-info"
        )

    for entry in entries:
        if entry.missing:
            _nul._emit_missing(entry, mode=mode)
            continue

        if entry.oid is None:
            raise RuntimeError("present inventory entry has no local SHA-256 identity")
        oid = entry.oid.lower()
        if entry.type_name == "commit" and entry.path is None and oid in boundary_oids:
            _nul._emit_fields(oid, "boundary=yes")
            continue
        _nul._emit_present(entry, no_object_names=parsed["no_object_names"])
    return 0


def _render(
    entries: Sequence[PromisorObjectInventoryEntry],
    *,
    parsed,
    mode: str,
    boundary_oids: frozenset[str],
    edges: Sequence[str] = (),
) -> int:
    if parsed["in_commit_order_nul"]:
        if edges:
            raise RuntimeError("internal error: NUL in-commit-order traversal received object edges")
        return _render_nul(
            entries,
            parsed=parsed,
            mode=mode,
            boundary_oids=boundary_oids,
        )

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
    if edges and boundary_oids:
        entries, boundary_oids = _dedupe_edge_boundary_overlap(
            entries,
            boundary_oids=boundary_oids,
            edges=edges,
        )
    return _render(
        entries,
        parsed=parsed,
        mode=parsed["in_commit_order_missing_mode"],
        boundary_oids=boundary_oids,
        edges=edges,
    )
