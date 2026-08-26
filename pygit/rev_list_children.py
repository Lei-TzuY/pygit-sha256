"""Child metadata presentation for ``rev-list --children``.

Git computes child links from the revision walk before output limiting.  That
means a commit removed by ``--skip`` can still appear as a child of the first
emitted commit.  This module keeps that detail separate from the core commit
selector while reusing the same revision semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple

from .repo import Repository
from .rev_list import rev_list
from .rev_list_parents import parent_oids


@dataclass(frozen=True)
class RevListChildEntry:
    """One selected commit together with the children printed by ``--children``."""

    oid: str
    children: Tuple[str, ...]
    side: Optional[str] = None


def _child_map(
    repo: Repository,
    revisions: Sequence[str],
    *,
    all_refs: bool,
    first_parent: bool,
    topo_order: bool,
    reverse: bool,
    left_right: bool,
) -> Dict[str, Tuple[str, ...]]:
    """Return child links for the complete selected revision set.

    ``skip`` and ``max-count`` are deliberately absent. Git builds child
    metadata before those presentation limits. Child ordering follows the
    unbounded rev-list order so output remains deterministic.
    """

    complete = rev_list(
        repo,
        revisions,
        all_refs=all_refs,
        first_parent=first_parent,
        topo_order=topo_order,
        reverse=reverse,
        skip=0,
        max_count=0,
        left_right=left_right,
    )
    selected = {entry.oid.lower() for entry in complete}
    children: Dict[str, list[str]] = {oid: [] for oid in selected}

    for entry in complete:
        child = entry.oid.lower()
        parents = parent_oids(repo, child)
        if first_parent:
            parents = parents[:1]
        for parent in parents:
            parent = parent.lower()
            if parent in selected:
                children[parent].append(child)

    return {oid: tuple(values) for oid, values in children.items()}


def rev_list_children(
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
) -> Tuple[RevListChildEntry, ...]:
    """Return selected commits with Git-style child metadata.

    Selection, ordering, exclusions, symmetric ranges, and side markers are
    delegated to :func:`rev_list`. Child links are derived from the complete
    selected walk before ``skip`` / ``max_count`` are applied, matching native
    Git. ``--first-parent`` restricts both traversal and child edges to first
    parent links. Shallow-boundary parent suppression is inherited from
    :func:`parent_oids`.
    """

    child_map = _child_map(
        repo,
        revisions,
        all_refs=all_refs,
        first_parent=first_parent,
        topo_order=topo_order,
        reverse=reverse,
        left_right=left_right,
    )
    entries = rev_list(
        repo,
        revisions,
        all_refs=all_refs,
        first_parent=first_parent,
        topo_order=topo_order,
        reverse=reverse,
        skip=skip,
        max_count=max_count,
        left_right=left_right,
    )
    return tuple(
        RevListChildEntry(
            oid=entry.oid.lower(),
            children=child_map.get(entry.oid.lower(), ()),
            side=entry.side,
        )
        for entry in entries
    )
