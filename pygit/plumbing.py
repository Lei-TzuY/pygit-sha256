"""
pygit/plumbing.py
=================
Graph and reference plumbing that is useful independently of the porcelain
Repository methods.

The helpers here intentionally operate on pygit's SHA-256 object database and
``.pygit/refs`` namespace.  They do not assume native Git's SHA-1 object size.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Dict, List, Sequence, Set, Tuple

from .objects import CommitObject, TagObject
from .repo import Repository


_HEX = frozenset("0123456789abcdef")


def _is_oid(value: str) -> bool:
    return len(value) == 64 and all(ch in _HEX for ch in value.lower())


def resolve_commit(repo: Repository, revision: str) -> str:
    """Resolve *revision* and peel annotated tags until a commit is reached."""
    sha = repo.rev_parse(revision)
    seen: Set[str] = set()

    while True:
        if sha in seen:
            raise RuntimeError(f"Tag cycle while resolving {revision!r}")
        seen.add(sha)

        obj = repo.store.read(sha)
        if isinstance(obj, CommitObject):
            return sha
        if isinstance(obj, TagObject):
            sha = obj.target_sha
            continue
        raise RuntimeError(f"Revision {revision!r} does not name a commit")


def _shallow_boundaries(repo: Repository) -> Set[str]:
    """Return commit IDs that must be treated as roots in a shallow clone."""
    path = repo.pygit_dir / "shallow"
    if not path.exists():
        return set()
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if _is_oid(line.strip())
    }


def ancestor_distances(repo: Repository, start: str) -> Dict[str, int]:
    """Return the shortest parent-edge distance from *start* to each ancestor."""
    shallow = _shallow_boundaries(repo)
    distances: Dict[str, int] = {}
    queue = deque([(start, 0)])

    while queue:
        sha, distance = queue.popleft()
        previous = distances.get(sha)
        if previous is not None and previous <= distance:
            continue
        distances[sha] = distance

        if sha in shallow:
            continue

        obj = repo.store.read(sha)
        if not isinstance(obj, CommitObject):
            raise RuntimeError(f"Object {sha} in commit ancestry is not a commit")
        for parent in obj.parents:
            queue.append((parent, distance + 1))

    return distances


def merge_bases(repo: Repository, left: str, right: str) -> List[str]:
    """
    Return all *best* common ancestors of two revisions.

    A common ancestor is discarded when it is itself an ancestor of another
    common ancestor.  The remaining commits are Git's merge-base candidates.
    Results are ordered deterministically by graph distance and then object ID.
    """
    left_sha = resolve_commit(repo, left)
    right_sha = resolve_commit(repo, right)
    left_dist = ancestor_distances(repo, left_sha)
    right_dist = ancestor_distances(repo, right_sha)
    common = set(left_dist).intersection(right_dist)
    if not common:
        return []

    # Mark common ancestors that are dominated by a newer common ancestor.
    dominated: Set[str] = set()
    for candidate in common:
        candidate_ancestors = ancestor_distances(repo, candidate)
        dominated.update((common.intersection(candidate_ancestors)) - {candidate})

    best = common - dominated
    return sorted(
        best,
        key=lambda sha: (
            max(left_dist[sha], right_dist[sha]),
            left_dist[sha] + right_dist[sha],
            sha,
        ),
    )


def is_ancestor(repo: Repository, ancestor: str, descendant: str) -> bool:
    """Return whether *ancestor* is reachable from *descendant* via parents."""
    ancestor_sha = resolve_commit(repo, ancestor)
    descendant_sha = resolve_commit(repo, descendant)
    return ancestor_sha in ancestor_distances(repo, descendant_sha)


def _matches_ref_pattern(refname: str, pattern: str) -> bool:
    """Match Git show-ref style suffix patterns on complete path components."""
    pattern = pattern.strip("/")
    return bool(pattern) and (
        refname == pattern or refname.endswith("/" + pattern)
    )


def list_refs(
    repo: Repository,
    *,
    include_head: bool = False,
    heads: bool = False,
    tags: bool = False,
    patterns: Sequence[str] = (),
) -> List[Tuple[str, str]]:
    """
    Return ``(oid, refname)`` pairs from the repository ref namespace.

    With neither *heads* nor *tags*, all refs below ``refs/`` are returned.
    Supplying either filter restricts output to the selected namespaces.
    Patterns follow ``git show-ref`` suffix semantics rather than substring
    matching (``main`` matches ``refs/heads/main`` but not ``domain``).
    """
    result: List[Tuple[str, str]] = []

    if include_head:
        head = repo.refs.resolve_head()
        if head and (not patterns or any(_matches_ref_pattern("HEAD", p) for p in patterns)):
            result.append((head, "HEAD"))

    refs_root = repo.pygit_dir / "refs"
    if not refs_root.exists():
        return result

    selected_namespaces: Set[str] = set()
    if heads:
        selected_namespaces.add("heads")
    if tags:
        selected_namespaces.add("tags")

    for path in sorted(p for p in refs_root.rglob("*") if p.is_file()):
        relative = path.relative_to(refs_root).as_posix()
        namespace = relative.split("/", 1)[0]
        if selected_namespaces and namespace not in selected_namespaces:
            continue

        refname = f"refs/{relative}"
        if patterns and not any(_matches_ref_pattern(refname, p) for p in patterns):
            continue

        oid = path.read_text(encoding="utf-8").strip()
        if not _is_oid(oid):
            raise RuntimeError(f"Malformed ref {refname}: expected a 64-hex object ID")
        result.append((oid, refname))

    return result


def verify_ref(repo: Repository, refname: str) -> Tuple[str, str]:
    """Resolve one exact, fully-qualified ``refs/...`` name."""
    if not refname.startswith("refs/"):
        raise ValueError("--verify requires an exact ref name beginning with 'refs/'")

    refs_root = (repo.pygit_dir / "refs").resolve()
    path = (repo.pygit_dir / refname).resolve()
    try:
        path.relative_to(refs_root)
    except ValueError as exc:
        raise ValueError(f"Invalid ref name: {refname!r}") from exc

    if not path.is_file():
        raise KeyError(refname)
    oid = path.read_text(encoding="utf-8").strip()
    if not _is_oid(oid):
        raise RuntimeError(f"Malformed ref {refname}: expected a 64-hex object ID")
    return oid, refname


def peel_oid(repo: Repository, oid: str) -> str:
    """Peel annotated tags and return the final target object ID."""
    current = oid
    seen: Set[str] = set()
    while True:
        if current in seen:
            raise RuntimeError(f"Tag cycle while peeling {oid}")
        seen.add(current)
        obj = repo.store.read(current)
        if not isinstance(obj, TagObject):
            return current
        current = obj.target_sha
