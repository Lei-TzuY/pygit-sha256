"""Commit-set traversal for script-facing ``rev-list`` plumbing.

The historical CLI supported only one start revision plus a small symmetric
range special case.  This module provides a reusable, read-only graph engine
for positive/negative revisions, two-dot and three-dot ranges, all-ref walks,
first-parent traversal, shallow boundaries, deterministic date/topological
ordering, and left/right symmetric-difference markers.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .objects import CommitObject, TagObject
from .plumbing import list_refs
from .repo import Repository
from .revision import resolve_revision


@dataclass(frozen=True)
class RevListEntry:
    """One selected commit and its optional symmetric-range side marker."""

    oid: str
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


def _commit(repo: Repository, oid: str) -> CommitObject:
    obj = repo.store.read(oid)
    if not isinstance(obj, CommitObject):
        raise RuntimeError(f"Object {oid} in commit traversal is not a commit")
    return obj


def _resolve_commitish(repo: Repository, revision: str) -> str:
    if not revision:
        revision = "HEAD"
    return resolve_revision(repo, revision + "^{commit}")


def _walk(repo: Repository, starts: Iterable[str], *, first_parent: bool) -> Set[str]:
    shallow = _shallow_boundaries(repo)
    seen: Set[str] = set()
    stack = list(starts)

    while stack:
        oid = stack.pop()
        if oid in seen:
            continue
        seen.add(oid)
        if oid in shallow:
            continue
        parents = _commit(repo, oid).parents
        if first_parent:
            if parents:
                stack.append(parents[0].lower())
        else:
            stack.extend(parent.lower() for parent in parents)
    return seen


def _commit_time(repo: Repository, oid: str, cache: Dict[str, CommitObject]) -> int:
    commit = cache.get(oid)
    if commit is None:
        commit = _commit(repo, oid)
        cache[oid] = commit
    committer = getattr(commit, "committer", None)
    if committer is not None:
        return int(getattr(committer, "timestamp", 0))
    author = getattr(commit, "author", None)
    return int(getattr(author, "timestamp", 0)) if author is not None else 0


def _date_order(repo: Repository, selected: Set[str]) -> List[str]:
    cache: Dict[str, CommitObject] = {}
    return sorted(selected, key=lambda oid: (-_commit_time(repo, oid, cache), oid))


def _topological_order(repo: Repository, selected: Set[str]) -> List[str]:
    """Return children before parents, using commit time as a stable tie-break."""
    if not selected:
        return []

    commits = {oid: _commit(repo, oid) for oid in selected}
    child_counts = {oid: 0 for oid in selected}
    for child, commit in commits.items():
        for parent in commit.parents:
            parent = parent.lower()
            if parent in child_counts:
                child_counts[parent] += 1

    ready: List[Tuple[int, str]] = []
    for oid, count in child_counts.items():
        if count == 0:
            timestamp = int(getattr(getattr(commits[oid], "committer", None), "timestamp", 0))
            heapq.heappush(ready, (-timestamp, oid))

    ordered: List[str] = []
    while ready:
        _, oid = heapq.heappop(ready)
        ordered.append(oid)
        for parent in commits[oid].parents:
            parent = parent.lower()
            if parent not in child_counts:
                continue
            child_counts[parent] -= 1
            if child_counts[parent] == 0:
                timestamp = int(getattr(getattr(commits[parent], "committer", None), "timestamp", 0))
                heapq.heappush(ready, (-timestamp, parent))

    if len(ordered) != len(selected):
        raise RuntimeError("commit graph cycle detected during topological ordering")
    return ordered


def _ref_commit_tip(repo: Repository, refname: str) -> Optional[str]:
    """Peel one ref to a commit, returning None only for a valid non-commit tip."""
    oid = resolve_revision(repo, refname)
    seen: Set[str] = set()
    while True:
        if oid in seen:
            raise RuntimeError(f"Tag cycle while resolving {refname!r}")
        seen.add(oid)
        obj = repo.store.read(oid)
        if isinstance(obj, CommitObject):
            return oid
        if isinstance(obj, TagObject):
            oid = obj.target_sha.lower()
            continue
        return None


def _all_ref_tips(repo: Repository) -> List[str]:
    tips: List[str] = []
    seen: Set[str] = set()
    for _, refname in list_refs(repo):
        oid = _ref_commit_tip(repo, refname)
        if oid is None:
            continue
        if oid not in seen:
            seen.add(oid)
            tips.append(oid)
    return tips


def _split_range(token: str, marker: str) -> Tuple[str, str]:
    if token.count(marker) != 1:
        raise ValueError(f"invalid revision range: {token!r}")
    left, right = token.split(marker, 1)
    return left or "HEAD", right or "HEAD"


def _normal_revisions(repo: Repository, revisions: Sequence[str]) -> Tuple[List[str], List[str]]:
    positive: List[str] = []
    negative: List[str] = []

    for token in revisions:
        if not token:
            raise ValueError("empty revision")
        if "..." in token:
            raise ValueError("symmetric ranges must be used as the sole explicit revision")
        if token.startswith("^"):
            expression = token[1:]
            if not expression or ".." in expression:
                raise ValueError(f"invalid negative revision: {token!r}")
            negative.append(_resolve_commitish(repo, expression))
            continue
        if ".." in token:
            left, right = _split_range(token, "..")
            negative.append(_resolve_commitish(repo, left))
            positive.append(_resolve_commitish(repo, right))
            continue
        positive.append(_resolve_commitish(repo, token))

    return positive, negative


def rev_list(
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
) -> Tuple[RevListEntry, ...]:
    """Select commits using Git-style positive/negative revision-set semantics.

    ``A..B`` means commits reachable from ``B`` but not ``A``. ``A...B`` is
    the symmetric difference of the two ancestry sets.  Negative ``^REV``
    arguments subtract that revision and all ancestors.  With no explicit
    revision and without ``all_refs``, ``HEAD`` is the default positive tip.
    """
    if skip < 0:
        raise ValueError("--skip must be non-negative")
    if max_count < 0:
        raise ValueError("--max-count must be non-negative")

    symmetric = [token for token in revisions if "..." in token]
    sides: Dict[str, str] = {}

    if symmetric:
        if len(revisions) != 1 or all_refs:
            raise ValueError("a symmetric A...B range cannot be mixed with other revisions or --all")
        left, right = _split_range(symmetric[0], "...")
        left_tip = _resolve_commitish(repo, left)
        right_tip = _resolve_commitish(repo, right)
        left_set = _walk(repo, [left_tip], first_parent=first_parent)
        right_set = _walk(repo, [right_tip], first_parent=first_parent)
        selected = left_set ^ right_set
        for oid in selected:
            sides[oid] = "<" if oid in left_set else ">"
    else:
        positive, negative = _normal_revisions(repo, revisions)
        if all_refs:
            positive.extend(_all_ref_tips(repo))
        if not revisions and not all_refs:
            positive.append(_resolve_commitish(repo, "HEAD"))
        if not positive:
            raise ValueError("rev-list requires at least one positive revision or --all")

        included = _walk(repo, positive, first_parent=first_parent)
        excluded = _walk(repo, negative, first_parent=first_parent) if negative else set()
        selected = included - excluded

    if left_right and not symmetric:
        raise ValueError("--left-right requires exactly one A...B symmetric range")

    ordered = _topological_order(repo, selected) if topo_order else _date_order(repo, selected)
    if skip:
        ordered = ordered[skip:]
    if max_count:
        ordered = ordered[:max_count]
    if reverse:
        ordered.reverse()

    return tuple(RevListEntry(oid=oid, side=sides.get(oid) if left_right else None) for oid in ordered)
