"""Oldest-N commit limiting for ``rev-list --max-count-oldest``.

Git 2.55 added ``--max-count-oldest=<n>`` as the mirror of ``--max-count``:
select the oldest N commits that would otherwise be shown. Native Git rejects
combining it with ``--max-count`` or ``--skip``; the CLI enforces the same rule.

This module keeps the option composable with pygit's existing side filters,
parent-count filters, child metadata, boundaries, and object enumeration while
preserving the rule that ``--reverse`` is only a final presentation transform.
"""

from __future__ import annotations

from typing import List, Sequence, Set, Tuple

from .objects import CommitObject
from .pack_objects import reachable_objects
from .repo import Repository
from .rev_list import RevListEntry, _date_order, _object_exclusion_roots, _topological_order, rev_list
from .rev_list_boundary import RevListBoundaryEntry
from .rev_list_children import RevListChildEntry, rev_list_children
from .rev_list_object_names import RevListNamedObjectEntry, _visit_tree
from .rev_list_parent_filter import rev_list_parent_filter, rev_list_parent_filter_children
from .rev_list_parents import parent_oids
from .rev_list_sides import rev_list_sides


def normalize_oldest_count(value: int) -> int:
    """Validate a Git-style non-negative oldest-count limit."""

    if value < 0:
        raise ValueError("--max-count-oldest must be non-negative")
    return value


def _tail(entries: Sequence[object], count: int, *, reverse: bool) -> tuple:
    count = normalize_oldest_count(count)
    selected = list(entries[-count:]) if count else []
    if reverse:
        selected.reverse()
    return tuple(selected)


def rev_list_oldest(
    repo: Repository,
    revisions: Sequence[str] = (),
    *,
    max_count_oldest: int,
    all_refs: bool = False,
    first_parent: bool = False,
    topo_order: bool = False,
    reverse: bool = False,
    left_right: bool = False,
    left_only: bool = False,
    right_only: bool = False,
    min_parents: int = 0,
    max_parents: int = -1,
) -> Tuple[RevListEntry, ...]:
    """Return the oldest N commits after ordinary commit filtering.

    Parent-count and side filters are applied first. The oldest-N slice then
    selects the tail of the normal (newest-first / topo) stream. ``--reverse``
    reverses only that final selected slice, matching native Git's tests.
    """

    parent_filter_mode = min_parents > 0 or max_parents >= 0
    side_mode = left_right or left_only or right_only
    if parent_filter_mode:
        entries = rev_list_parent_filter(
            repo,
            revisions,
            all_refs=all_refs,
            first_parent=first_parent,
            topo_order=topo_order,
            reverse=False,
            skip=0,
            max_count=0,
            left_right=side_mode,
            left_only=left_only,
            right_only=right_only,
            min_parents=min_parents,
            max_parents=max_parents,
        )
    elif side_mode:
        entries = rev_list_sides(
            repo,
            revisions,
            all_refs=all_refs,
            first_parent=first_parent,
            topo_order=topo_order,
            reverse=False,
            skip=0,
            max_count=0,
            left_only=left_only,
            right_only=right_only,
        )
    else:
        entries = rev_list(
            repo,
            revisions,
            all_refs=all_refs,
            first_parent=first_parent,
            topo_order=topo_order,
            reverse=False,
            skip=0,
            max_count=0,
            left_right=False,
        )
    return _tail(entries, max_count_oldest, reverse=reverse)


def rev_list_oldest_children(
    repo: Repository,
    revisions: Sequence[str] = (),
    *,
    max_count_oldest: int,
    all_refs: bool = False,
    first_parent: bool = False,
    topo_order: bool = False,
    reverse: bool = False,
    left_right: bool = False,
    left_only: bool = False,
    right_only: bool = False,
    min_parents: int = 0,
    max_parents: int = -1,
) -> Tuple[RevListChildEntry, ...]:
    """Oldest-N records while retaining Phase-134 pre-limit child metadata."""

    parent_filter_mode = min_parents > 0 or max_parents >= 0
    side_mode = left_right or left_only or right_only
    if parent_filter_mode:
        entries = rev_list_parent_filter_children(
            repo,
            revisions,
            all_refs=all_refs,
            first_parent=first_parent,
            topo_order=topo_order,
            reverse=False,
            skip=0,
            max_count=0,
            left_right=side_mode,
            left_only=left_only,
            right_only=right_only,
            min_parents=min_parents,
            max_parents=max_parents,
        )
    else:
        entries = rev_list_children(
            repo,
            revisions,
            all_refs=all_refs,
            first_parent=first_parent,
            topo_order=topo_order,
            reverse=False,
            skip=0,
            max_count=0,
            left_right=side_mode,
        )
        if left_only:
            entries = tuple(entry for entry in entries if entry.side == "<")
        elif right_only:
            entries = tuple(entry for entry in entries if entry.side == ">")
    return _tail(entries, max_count_oldest, reverse=reverse)


def rev_list_oldest_boundary(
    repo: Repository,
    revisions: Sequence[str] = (),
    *,
    max_count_oldest: int,
    all_refs: bool = False,
    first_parent: bool = False,
    topo_order: bool = False,
    reverse: bool = False,
    side_mode: bool = False,
    left_only: bool = False,
    right_only: bool = False,
    min_parents: int = 0,
    max_parents: int = -1,
) -> Tuple[RevListBoundaryEntry, ...]:
    """Return oldest-N visible commits plus direct excluded boundaries."""

    selected = rev_list_oldest(
        repo,
        revisions,
        max_count_oldest=max_count_oldest,
        all_refs=all_refs,
        first_parent=first_parent,
        topo_order=topo_order,
        reverse=False,
        left_right=side_mode,
        left_only=left_only,
        right_only=right_only,
        min_parents=min_parents,
        max_parents=max_parents,
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

    boundary_order = _topological_order(repo, boundary_set) if topo_order else _date_order(repo, boundary_set)
    output: List[RevListBoundaryEntry] = [
        RevListBoundaryEntry(oid=entry.oid.lower(), side=entry.side, boundary=False)
        for entry in selected
    ]
    output.extend(
        RevListBoundaryEntry(oid=oid, side="<" if side_mode else None, boundary=True)
        for oid in boundary_order
    )
    if reverse:
        output.reverse()
    return tuple(output)


def rev_list_oldest_named_objects(
    repo: Repository,
    revisions: Sequence[str] = (),
    *,
    max_count_oldest: int,
    all_refs: bool = False,
    first_parent: bool = False,
    topo_order: bool = False,
    reverse: bool = False,
    min_parents: int = 0,
    max_parents: int = -1,
) -> Tuple[RevListNamedObjectEntry, ...]:
    """Return object closure for only the oldest-N selected commits."""

    commits = rev_list_oldest(
        repo,
        revisions,
        max_count_oldest=max_count_oldest,
        all_refs=all_refs,
        first_parent=first_parent,
        topo_order=topo_order,
        reverse=reverse,
        min_parents=min_parents,
        max_parents=max_parents,
    )
    commit_oids = [entry.oid.lower() for entry in commits]
    if not commit_oids:
        return ()

    selected = reachable_objects(repo, commit_oids, follow_commit_parents=False)
    exclusion_roots = _object_exclusion_roots(repo, revisions, first_parent=first_parent)
    if exclusion_roots:
        selected.difference_update(
            reachable_objects(
                repo,
                exclusion_roots,
                follow_commit_parents=True,
                first_parent=first_parent,
            )
        )

    output: List[RevListNamedObjectEntry] = []
    seen: Set[str] = set()
    for oid in commit_oids:
        if oid not in selected or oid in seen:
            continue
        obj = repo.store.read(oid)
        if not isinstance(obj, CommitObject):
            raise RuntimeError(f"Object {oid} in rev-list commit output is not a commit")
        seen.add(oid)
        output.append(RevListNamedObjectEntry(oid=oid, type_name="commit"))

    for oid in commit_oids:
        if oid not in selected:
            continue
        commit = repo.store.read(oid)
        if not isinstance(commit, CommitObject):
            raise RuntimeError(f"Object {oid} in rev-list traversal is not a commit")
        _visit_tree(
            repo,
            commit.tree.lower(),
            "",
            selected=selected,
            seen=seen,
            output=output,
        )

    for oid in sorted(selected - seen):
        obj = repo.store.read(oid)
        output.append(
            RevListNamedObjectEntry(
                oid=oid,
                type_name=obj.type_name.decode("ascii"),
                path=None,
            )
        )
    return tuple(output)
