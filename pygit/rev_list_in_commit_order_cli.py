"""Git-style ``rev-list --objects --in-commit-order`` traversal.

The existing promisor object inventory deliberately emits all selected commits
before walking their snapshots.  Git's ``--in-commit-order`` mode changes only
that presentation order: each selected commit is emitted immediately before the
first tree/blob objects reached from that commit, while object identity remains
globally deduplicated across the walk.

Phase259 keeps this mode independent from boundary, object-edge, NUL, filter,
and disk-usage framing.  Ordinary repositories and the existing metadata-only
promisor missing modes share the same ordered inventory and never materialize a
promised object merely to establish traversal order.
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
    if "--boundary" in argv:
        raise ValueError("rev-list --in-commit-order with --boundary is not yet supported")
    if "--objects-edge" in argv:
        raise ValueError("rev-list --in-commit-order with --objects-edge is not yet supported")
    if any(arg == "--disk-usage" or arg.startswith("--disk-usage=") for arg in argv):
        raise ValueError("rev-list --in-commit-order with --disk-usage is not yet supported")
    if any(
        arg.startswith("--filter=")
        or arg in {"--filter-print-omitted", "--filter-provided-objects"}
        for arg in argv
    ):
        raise ValueError("rev-list --in-commit-order with --filter is not yet supported")

    object_modes = [arg for arg in argv if arg in {"--objects", "--objects-edge"}]
    if object_modes != ["--objects"]:
        raise ValueError("rev-list --in-commit-order currently requires exactly one --objects")

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

    projected: list[str] = []
    for arg in argv:
        if arg == _IN_COMMIT_ORDER:
            continue
        if arg == "--missing=print":
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
    return parsed


def _ordered_inventory(repo, parsed) -> Tuple[PromisorObjectInventoryEntry, ...]:
    """Build commit/snapshot-interleaved inventory with global object dedupe."""

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
    if not commits:
        return ()

    output: list[PromisorObjectInventoryEntry] = []
    seen: set[tuple[str, str]] = set()

    for selected in commits:
        oid = selected.oid.lower()
        obj = repo.store.read(oid)
        if not isinstance(obj, CommitObject):
            raise RuntimeError(f"Object {oid} in rev-list traversal is not a commit")
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
        output = [entry for entry in output if _inventory._key(entry) not in excluded]

    return tuple(output)


def _plain_missing(entry: PromisorObjectInventoryEntry) -> str:
    if entry.native_oid is None:
        raise RuntimeError("missing inventory entry has no native object identity")
    return f"?{entry.native_oid.lower()}"


def _render(entries: Sequence[PromisorObjectInventoryEntry], *, parsed, mode: str) -> int:
    missing = tuple(entry for entry in entries if entry.missing)
    if mode == "ordinary" and missing:
        native = missing[0].native_oid or "unknown"
        raise RuntimeError(
            f"missing object {native}; use --missing=allow-promisor, print, or print-info"
        )

    if mode == "print-info":
        present_count = _promisor._render_print_info_entries(
            entries,
            no_object_names=parsed["no_object_names"],
            emit_present=not parsed["count"],
            skip_top_level_commits=False,
        )
        if parsed["count"]:
            print(present_count)
        return 0

    if mode == "print":
        present_count = 0
        for entry in entries:
            if entry.missing:
                print(_plain_missing(entry))
                continue
            present_count += 1
            if not parsed["count"]:
                _promisor._print_present(
                    entry,
                    no_object_names=parsed["no_object_names"],
                )
        if parsed["count"]:
            print(present_count)
        return 0

    present = tuple(entry for entry in entries if not entry.missing)
    if parsed["count"]:
        print(len(present))
        return 0
    for entry in present:
        _promisor._print_present(
            entry,
            no_object_names=parsed["no_object_names"],
        )
    return 0


def try_run_rev_list_in_commit_order(argv: Sequence[str]) -> Optional[int]:
    """Handle ordered object traversal without materializing promises."""

    parsed = _parse(argv)
    if parsed is None:
        return None

    repo = _promisor._find_repo()
    entries = _ordered_inventory(repo, parsed)
    return _render(
        entries,
        parsed=parsed,
        mode=parsed["in_commit_order_missing_mode"],
    )
