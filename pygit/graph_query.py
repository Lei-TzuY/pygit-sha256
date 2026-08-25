"""Multi-commit graph plumbing used by advanced ``merge-base`` modes.

This module builds on :mod:`pygit.plumbing` without changing the historical
pairwise API.  All revision inputs are peeled to commits, ancestry walks obey
``.pygit/shallow`` boundaries, and result ordering is deterministic.
"""

from __future__ import annotations

from collections import deque
from typing import Dict, Iterable, List, Sequence, Set

from .objects import CommitObject
from .plumbing import ancestor_distances, merge_bases, resolve_commit
from .repo import Repository


_HEX = frozenset("0123456789abcdef")


def _is_oid(value: str) -> bool:
    return len(value) == 64 and all(ch in _HEX for ch in value.lower())


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
        raise RuntimeError(f"Object {oid} in commit ancestry is not a commit")
    return obj


def _generation(
    repo: Repository,
    oid: str,
    shallow: Set[str],
    memo: Dict[str, int],
    visiting: Set[str],
) -> int:
    """Return a memoized topological generation number for *oid*."""
    if oid in memo:
        return memo[oid]
    if oid in visiting:
        raise RuntimeError(f"Commit graph cycle detected at {oid}")

    visiting.add(oid)
    try:
        if oid in shallow:
            value = 1
        else:
            parents = _commit(repo, oid).parents
            value = 1 + max(
                (_generation(repo, parent, shallow, memo, visiting) for parent in parents),
                default=0,
            )
        memo[oid] = value
        return value
    finally:
        visiting.discard(oid)


def _best_common(
    repo: Repository,
    common: Set[str],
    distance_maps: Sequence[Dict[str, int]],
) -> List[str]:
    """Remove common ancestors dominated by a newer common ancestor.

    Candidates are visited newest-generation first.  Once a best candidate is
    selected, a single parent walk marks every older common candidate reachable
    from it as dominated.  ``expanded`` is shared across selected candidates so
    overlapping ancestry is traversed only once.
    """
    if not common:
        return []

    shallow = _shallow_boundaries(repo)
    generations: Dict[str, int] = {}
    visiting: Set[str] = set()
    ordered = sorted(
        common,
        key=lambda oid: (
            -_generation(repo, oid, shallow, generations, visiting),
            oid,
        ),
    )

    best: List[str] = []
    dominated: Set[str] = set()
    expanded: Set[str] = set()

    for candidate in ordered:
        if candidate in dominated:
            continue
        best.append(candidate)
        if candidate in shallow:
            continue

        queue = deque(_commit(repo, candidate).parents)
        while queue:
            oid = queue.popleft()
            if oid in expanded:
                continue
            expanded.add(oid)
            if oid in common:
                dominated.add(oid)
            if oid in shallow:
                continue
            queue.extend(_commit(repo, oid).parents)

    def rank(oid: str) -> tuple[int, int, str]:
        distances = [mapping[oid] for mapping in distance_maps]
        return max(distances), sum(distances), oid

    return sorted(best, key=rank)


def merge_bases_many(repo: Repository, revisions: Sequence[str]) -> List[str]:
    """Return merge bases for Git's default multi-commit interpretation.

    With more than two commits Git compares the first commit with a hypothetical
    merge commit whose parents are the remaining commits.  The hypothetical
    commit does not need to be materialized: its ancestry is the union of the
    remaining ancestry sets.
    """
    if len(revisions) < 2:
        raise ValueError("merge-base requires at least two commits")
    if len(revisions) == 2:
        return merge_bases(repo, revisions[0], revisions[1])

    resolved = [resolve_commit(repo, revision) for revision in revisions]
    maps = [ancestor_distances(repo, oid) for oid in resolved]
    left = maps[0]

    # A synthetic merge commit is one edge above each remaining tip.
    right_union: Dict[str, int] = {}
    for mapping in maps[1:]:
        for oid, distance in mapping.items():
            merged_distance = distance + 1
            previous = right_union.get(oid)
            if previous is None or merged_distance < previous:
                right_union[oid] = merged_distance

    common = set(left).intersection(right_union)
    return _best_common(repo, common, [left, right_union])


def octopus_merge_bases(repo: Repository, revisions: Sequence[str]) -> List[str]:
    """Return best common ancestors shared by every supplied commit."""
    if len(revisions) < 2:
        raise ValueError("merge-base --octopus requires at least two commits")

    resolved = [resolve_commit(repo, revision) for revision in revisions]
    maps = [ancestor_distances(repo, oid) for oid in resolved]
    common = set(maps[0])
    for mapping in maps[1:]:
        common.intersection_update(mapping)
        if not common:
            return []
    return _best_common(repo, common, maps)


def independent_commits(repo: Repository, revisions: Sequence[str]) -> List[str]:
    """Return supplied commits not reachable from any other supplied commit."""
    if not revisions:
        raise ValueError("merge-base --independent requires at least one commit")

    resolved: List[str] = []
    seen: Set[str] = set()
    for revision in revisions:
        oid = resolve_commit(repo, revision)
        if oid not in seen:
            seen.add(oid)
            resolved.append(oid)

    ancestry = {oid: ancestor_distances(repo, oid) for oid in resolved}
    result = []
    for oid in resolved:
        if any(oid in ancestry[other] for other in resolved if other != oid):
            continue
        result.append(oid)
    return result
