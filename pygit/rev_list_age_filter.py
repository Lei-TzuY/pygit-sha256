"""Timestamp filtering for ``rev-list --max-age`` / ``--min-age``.

Git's plumbing names are historical: ``--max-age=<timestamp>`` keeps commits
newer than the timestamp, while ``--min-age=<timestamp>`` keeps commits older
than it.  Both comparisons are strict.  Filtering changes emitted commits but
not ancestry traversal, and happens before ordinary count/skip/oldest limits.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Set, Tuple

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


def _timestamp(repo: Repository, oid: str) -> int:
    obj = repo.store.read(oid)
    if not isinstance(obj, CommitObject):
        raise RuntimeError(f"Object {oid} in rev-list traversal is not a commit")
    committer = getattr(obj, "committer", None)
    if committer is not None:
        return int(getattr(committer, "timestamp", 0))
    author = getattr(obj, "author", None)
    return int(getattr(author, "timestamp", 0)) if author is not None else 0


def _matches_age(
    repo: Repository,
    oid: str,
    *,
    max_age: Optional[int],
    min_age: Optional[int],
) -> bool:
    timestamp = _timestamp(repo, oid)
    if max_age is not None and timestamp <= max_age:
        return False
    if min_age is not None and timestamp >= min_age:
        return False
    return True


def _limit(
    entries: Sequence[object],
    *,
    skip: int,
    max_count: int,
    max_count_oldest: Optional[int],
    reverse: bool,
) -> tuple:
    if skip < 0:
        raise ValueError("--skip must be non-negative")
    if max_count < 0:
        raise ValueError("--max-count must be non-negative")
    if max_count_oldest is not None and max_count_oldest < 0:
        raise ValueError("--max-count-oldest must be non-negative")

    selected = list(entries)
    if max_count_oldest is not None:
        selected = selected[-max_count_oldest:] if max_count_oldest else []
    else:
        if skip:
            selected = selected[skip:]
        if max_count:
            selected = selected[:max_count]
    if reverse:
        selected.reverse()
    return tuple(selected)


def _base_entries(
    repo: Repository,
    revisions: Sequence[str],
    *,
    all_refs: bool,
    first_parent: bool,
    topo_order: bool,
    side_mode: bool,
    left_only: bool,
    right_only: bool,
    min_parents: int,
    max_parents: int,
) -> Tuple[RevListEntry, ...]:
    parent_filter_mode = min_parents > 0 or max_parents >= 0
    if parent_filter_mode:
        return rev_list_parent_filter(
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
    if side_mode:
        return rev_list_sides(
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
    return rev_list(
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


def rev_list_age_filter(
    repo: Repository,
    revisions: Sequence[str] = (),
    *,
    all_refs: bool = False,
    first_parent: bool = False,
    topo_order: bool = False,
    reverse: bool = False,
    skip: int = 0,
    max_count: int = 0,
    max_count_oldest: Optional[int] = None,
    left_right: bool = False,
    left_only: bool = False,
    right_only: bool = False,
    min_parents: int = 0,
    max_parents: int = -1,
    max_age: Optional[int] = None,
    min_age: Optional[int] = None,
) -> Tuple[RevListEntry, ...]:
    """Return commits after strict committer-timestamp filtering."""

    side_mode = left_right or left_only or right_only
    entries = _base_entries(
        repo,
        revisions,
        all_refs=all_refs,
        first_parent=first_parent,
        topo_order=topo_order,
        side_mode=side_mode,
        left_only=left_only,
        right_only=right_only,
        min_parents=min_parents,
        max_parents=max_parents,
    )
    filtered = [
        entry
        for entry in entries
        if _matches_age(repo, entry.oid, max_age=max_age, min_age=min_age)
    ]
    return _limit(
        filtered,
        skip=skip,
        max_count=max_count,
        max_count_oldest=max_count_oldest,
        reverse=reverse,
    )


def rev_list_age_filter_children(
    repo: Repository,
    revisions: Sequence[str] = (),
    *,
    all_refs: bool = False,
    first_parent: bool = False,
    topo_order: bool = False,
    reverse: bool = False,
    skip: int = 0,
    max_count: int = 0,
    max_count_oldest: Optional[int] = None,
    left_right: bool = False,
    left_only: bool = False,
    right_only: bool = False,
    min_parents: int = 0,
    max_parents: int = -1,
    max_age: Optional[int] = None,
    min_age: Optional[int] = None,
) -> Tuple[RevListChildEntry, ...]:
    """Age-filter records while preserving pre-limit child metadata."""

    side_mode = left_right or left_only or right_only
    parent_filter_mode = min_parents > 0 or max_parents >= 0
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

    filtered = [
        entry
        for entry in entries
        if _matches_age(repo, entry.oid, max_age=max_age, min_age=min_age)
    ]
    return _limit(
        filtered,
        skip=skip,
        max_count=max_count,
        max_count_oldest=max_count_oldest,
        reverse=reverse,
    )


def rev_list_age_filter_boundary(
    repo: Repository,
    revisions: Sequence[str] = (),
    *,
    all_refs: bool = False,
    first_parent: bool = False,
    topo_order: bool = False,
    reverse: bool = False,
    skip: int = 0,
    max_count: int = 0,
    max_count_oldest: Optional[int] = None,
    side_mode: bool = False,
    left_only: bool = False,
    right_only: bool = False,
    min_parents: int = 0,
    max_parents: int = -1,
    max_age: Optional[int] = None,
    min_age: Optional[int] = None,
) -> Tuple[RevListBoundaryEntry, ...]:
    """Return age-filtered visible commits plus adjacent excluded parents."""

    selected = rev_list_age_filter(
        repo,
        revisions,
        all_refs=all_refs,
        first_parent=first_parent,
        topo_order=topo_order,
        reverse=False,
        skip=skip,
        max_count=max_count,
        max_count_oldest=max_count_oldest,
        left_right=side_mode,
        left_only=left_only,
        right_only=right_only,
        min_parents=min_parents,
        max_parents=max_parents,
        max_age=max_age,
        min_age=min_age,
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


def rev_list_age_filter_named_objects(
    repo: Repository,
    revisions: Sequence[str] = (),
    *,
    all_refs: bool = False,
    first_parent: bool = False,
    topo_order: bool = False,
    reverse: bool = False,
    skip: int = 0,
    max_count: int = 0,
    max_count_oldest: Optional[int] = None,
    min_parents: int = 0,
    max_parents: int = -1,
    max_age: Optional[int] = None,
    min_age: Optional[int] = None,
) -> Tuple[RevListNamedObjectEntry, ...]:
    """Return object closure for only the age-filtered selected commits."""

    commits = rev_list_age_filter(
        repo,
        revisions,
        all_refs=all_refs,
        first_parent=first_parent,
        topo_order=topo_order,
        reverse=reverse,
        skip=skip,
        max_count=max_count,
        max_count_oldest=max_count_oldest,
        min_parents=min_parents,
        max_parents=max_parents,
        max_age=max_age,
        min_age=min_age,
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
