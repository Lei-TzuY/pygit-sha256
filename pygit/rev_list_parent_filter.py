"""Parent-count limiting for ``rev-list`` selection.

Git treats ``--merges`` / ``--no-merges`` as shorthand for parent-count
filters.  These filters affect which traversed commits are emitted, but they do
not change ancestry traversal itself.  This module layers that behaviour over
the existing revision engine so filtering happens before ``--skip`` /
``--max-count`` and before the final ``--reverse`` presentation transform.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Sequence, Set, Tuple

from .objects import CommitObject
from .pack_objects import reachable_objects
from .repo import Repository
from .rev_list import RevListEntry, _date_order, _object_exclusion_roots, _topological_order, rev_list
from .rev_list_boundary import RevListBoundaryEntry
from .rev_list_children import RevListChildEntry, rev_list_children
from .rev_list_object_names import RevListNamedObjectEntry, _visit_tree
from .rev_list_parents import parent_oids
from .rev_list_sides import rev_list_sides


def normalize_parent_limits(min_parents: int = 0, max_parents: int = -1) -> Tuple[int, int]:
    """Normalize Git-style parent-count limits.

    ``min_parents`` must be non-negative.  Any negative ``max_parents`` means
    no upper bound, matching Git's documented ``--max-parents=-1`` reset form.
    """

    if min_parents < 0:
        raise ValueError("--min-parents must be non-negative")
    return min_parents, -1 if max_parents < 0 else max_parents


def _matches(repo: Repository, oid: str, *, min_parents: int, max_parents: int) -> bool:
    count = len(parent_oids(repo, oid))
    if count < min_parents:
        return False
    if max_parents >= 0 and count > max_parents:
        return False
    return True


def _limit(entries: List[object], *, skip: int, max_count: int, reverse: bool) -> tuple:
    if skip < 0:
        raise ValueError("--skip must be non-negative")
    if max_count < 0:
        raise ValueError("--max-count must be non-negative")
    if skip:
        entries = entries[skip:]
    if max_count:
        entries = entries[:max_count]
    if reverse:
        entries.reverse()
    return tuple(entries)


def rev_list_parent_filter(
    repo: Repository,
    revisions: Sequence[str] = (),
    *,
    all_refs: bool = False,
    first_parent: bool = False,
    topo_order: bool = False,
    reverse: bool = False,
    skip: int = 0,
    max_count: int = 0,
    left_right: bool = False,
    left_only: bool = False,
    right_only: bool = False,
    min_parents: int = 0,
    max_parents: int = -1,
) -> Tuple[RevListEntry, ...]:
    """Return commits after Git-style parent-count filtering.

    Traversal remains unchanged; only output records are filtered.  Parent
    filtering happens before output limits.  ``--first-parent`` therefore
    changes the walk but does not turn a stored merge commit into a one-parent
    commit for filtering purposes.
    """

    min_parents, max_parents = normalize_parent_limits(min_parents, max_parents)
    side_mode = left_right or left_only or right_only
    if side_mode:
        entries = list(
            rev_list_sides(
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
        )
    else:
        entries = list(
            rev_list(
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
        )

    entries = [
        entry
        for entry in entries
        if _matches(repo, entry.oid, min_parents=min_parents, max_parents=max_parents)
    ]
    return _limit(entries, skip=skip, max_count=max_count, reverse=reverse)


def rev_list_parent_filter_children(
    repo: Repository,
    revisions: Sequence[str] = (),
    *,
    all_refs: bool = False,
    first_parent: bool = False,
    topo_order: bool = False,
    reverse: bool = False,
    skip: int = 0,
    max_count: int = 0,
    left_right: bool = False,
    left_only: bool = False,
    right_only: bool = False,
    min_parents: int = 0,
    max_parents: int = -1,
) -> Tuple[RevListChildEntry, ...]:
    """Parent-filter records while preserving Phase-134 pre-limit child links."""

    min_parents, max_parents = normalize_parent_limits(min_parents, max_parents)
    entries = list(
        rev_list_children(
            repo,
            revisions,
            all_refs=all_refs,
            first_parent=first_parent,
            topo_order=topo_order,
            reverse=False,
            skip=0,
            max_count=0,
            left_right=left_right or left_only or right_only,
        )
    )
    if left_only:
        entries = [entry for entry in entries if entry.side == "<"]
    elif right_only:
        entries = [entry for entry in entries if entry.side == ">"]
    entries = [
        entry
        for entry in entries
        if _matches(repo, entry.oid, min_parents=min_parents, max_parents=max_parents)
    ]
    return _limit(entries, skip=skip, max_count=max_count, reverse=reverse)


def rev_list_parent_filter_boundary(
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
    min_parents: int = 0,
    max_parents: int = -1,
) -> Tuple[RevListBoundaryEntry, ...]:
    """Return parent-filtered visible commits plus their direct boundaries."""

    selected = rev_list_parent_filter(
        repo,
        revisions,
        all_refs=all_refs,
        first_parent=first_parent,
        topo_order=topo_order,
        reverse=False,
        skip=skip,
        max_count=max_count,
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


def rev_list_parent_filter_named_objects(
    repo: Repository,
    revisions: Sequence[str] = (),
    *,
    all_refs: bool = False,
    first_parent: bool = False,
    topo_order: bool = False,
    reverse: bool = False,
    skip: int = 0,
    max_count: int = 0,
    min_parents: int = 0,
    max_parents: int = -1,
) -> Tuple[RevListNamedObjectEntry, ...]:
    """Return the object closure of only the parent-filtered commit records."""

    commits = rev_list_parent_filter(
        repo,
        revisions,
        all_refs=all_refs,
        first_parent=first_parent,
        topo_order=topo_order,
        reverse=reverse,
        skip=skip,
        max_count=max_count,
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
