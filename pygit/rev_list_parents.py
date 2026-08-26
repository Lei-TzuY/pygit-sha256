"""Parent metadata presentation for ``rev-list --parents``.

The commit-set engine deliberately keeps traversal selection separate from
presentation.  This module layers Git-style parent records on top without
changing which commits are selected.  Raw commit parents are preserved even
when a parent is outside an excluded range; commits recorded as shallow
boundaries are presented as roots, matching traversal semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Set, Tuple

from .objects import CommitObject
from .repo import Repository
from .rev_list import rev_list


@dataclass(frozen=True)
class RevListParentEntry:
    """One selected commit together with the parents printed by ``--parents``."""

    oid: str
    parents: Tuple[str, ...]
    side: Optional[str] = None


def _is_oid(value: str) -> bool:
    return len(value) == 64 and all(ch in "0123456789abcdef" for ch in value.lower())


def _shallow_boundaries(repo: Repository) -> Set[str]:
    path = repo.pygit_dir / "shallow"
    if not path.exists():
        return set()
    return {
        line.strip().lower()
        for line in path.read_text(encoding="utf-8").splitlines()
        if _is_oid(line.strip())
    }


def rev_list_parents(
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
) -> Tuple[RevListParentEntry, ...]:
    """Return selected commits with Git-style raw parent metadata.

    Selection, ordering, exclusions, symmetric ranges, limits, and side markers
    are delegated to :func:`rev_list`.  Parent display itself is intentionally
    not filtered by the selected set: an excluded range boundary can still be a
    real parent of an emitted commit.  ``--first-parent`` changes traversal but
    does not rewrite the stored parent list.  A shallow boundary is the one
    exception because Git treats it as a synthetic root for revision walking.
    """

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
    shallow = _shallow_boundaries(repo)
    output = []
    for entry in entries:
        oid = entry.oid.lower()
        if oid in shallow:
            parents: Tuple[str, ...] = ()
        else:
            obj = repo.store.read(oid)
            if not isinstance(obj, CommitObject):
                raise RuntimeError(f"Object {oid} in rev-list traversal is not a commit")
            parents = tuple(parent.lower() for parent in obj.parents)
        output.append(RevListParentEntry(oid=oid, parents=parents, side=entry.side))
    return tuple(output)
