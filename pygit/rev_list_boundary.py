"""Boundary commit presentation for ``rev-list --boundary``.

Boundary calculation is intentionally layered on top of the existing revision
selector.  Limits such as ``--skip`` and ``--max-count`` are applied first;
parents immediately outside that visible commit set are then emitted as
excluded boundary records.  This matches Git's useful distinction between a
revision-range boundary and a boundary introduced by output limiting.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Set, Tuple

from .repo import Repository
from .rev_list import RevListEntry, _date_order, _topological_order, rev_list
from .rev_list_parents import parent_oids
from .rev_list_sides import rev_list_sides


@dataclass(frozen=True)
class RevListBoundaryEntry:
    """One selected or excluded boundary commit in display order."""

    oid: str
    side: Optional[str] = None
    boundary: bool = False


def _selected_entries(
    repo: Repository,
    revisions: Sequence[str],
    *,
    all_refs: bool,
    first_parent: bool,
    topo_order: bool,
    skip: int,
    max_count: int,
    side_mode: bool,
    left_only: bool,
    right_only: bool,
) -> Tuple[RevListEntry, ...]:
    if side_mode:
        return rev_list_sides(
            repo,
            revisions,
            all_refs=all_refs,
            first_parent=first_parent,
            topo_order=topo_order,
            reverse=False,
            skip=skip,
            max_count=max_count,
            left_only=left_only,
            right_only=right_only,
        )
    return rev_list(
        repo,
        revisions,
        all_refs=all_refs,
        first_parent=first_parent,
        topo_order=topo_order,
        reverse=False,
        skip=skip,
        max_count=max_count,
        left_right=False,
    )


def rev_list_boundary(
    repo: Repository,
    revisions: Sequence[str] = (),
    *,
    all_refs: bool = False,
    first_parent: bool = False,
    topo_order: bool = False,
    reverse: bool = False,
    skip: int = 0,
    max_count: int = 0,
    side_mode: bool = False,
    left_only: bool = False,
    right_only: bool = False,
) -> Tuple[RevListBoundaryEntry, ...]:
    """Return visible commits followed by their excluded boundary commits.

    A boundary is a traversal parent of a visible commit that is not itself in
    the visible set.  Therefore both revision exclusions and output limits can
    create boundaries.  Boundary candidates are ordered using the same
    date/topological ordering policy as the commit selector, then appended to
    the normal stream.  ``--reverse`` reverses the combined stream, matching
    native Git where boundary records move with the final presentation order.

    Side-aware walks retain selected ``<``/``>`` markers.  Boundary records are
    rendered with ``-`` by the CLI; internally they use the left-side marker so
    ``--left-right --boundary --count`` follows Git's counting behaviour.
    """

    selected = _selected_entries(
        repo,
        revisions,
        all_refs=all_refs,
        first_parent=first_parent,
        topo_order=topo_order,
        skip=skip,
        max_count=max_count,
        side_mode=side_mode,
        left_only=left_only,
        right_only=right_only,
    )
    if not selected:
        return ()

    visible: Set[str] = {entry.oid.lower() for entry in selected}
    boundary_set: Set[str] = set()
    for entry in selected:
        parents = parent_oids(repo, entry.oid)
        if first_parent:
            parents = parents[:1]
        for parent in parents:
            parent = parent.lower()
            if parent not in visible:
                boundary_set.add(parent)

    boundary_order = (
        _topological_order(repo, boundary_set)
        if topo_order
        else _date_order(repo, boundary_set)
    )

    output: List[RevListBoundaryEntry] = [
        RevListBoundaryEntry(oid=entry.oid.lower(), side=entry.side, boundary=False)
        for entry in selected
    ]
    output.extend(
        RevListBoundaryEntry(
            oid=oid,
            side="<" if side_mode else None,
            boundary=True,
        )
        for oid in boundary_order
    )
    if reverse:
        output.reverse()
    return tuple(output)


def boundary_children(
    repo: Repository,
    entries: Sequence[RevListBoundaryEntry],
    *,
    first_parent: bool = False,
) -> dict[str, Tuple[str, ...]]:
    """Return child metadata for boundary records in one selected stream.

    Native ``--children --boundary`` prints the visible child that reaches each
    excluded parent.  Selected-entry child metadata remains owned by the Phase
    134 helper; this function only supplies the otherwise-missing boundary
    records.
    """

    boundaries = {entry.oid for entry in entries if entry.boundary}
    children: dict[str, List[str]] = {oid: [] for oid in boundaries}
    for entry in entries:
        if entry.boundary:
            continue
        parents = parent_oids(repo, entry.oid)
        if first_parent:
            parents = parents[:1]
        for parent in parents:
            parent = parent.lower()
            if parent in boundaries and entry.oid not in children[parent]:
                children[parent].append(entry.oid)
    return {oid: tuple(values) for oid, values in children.items()}
