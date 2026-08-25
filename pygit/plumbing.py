"""
pygit/plumbing.py
=================
Graph and reference plumbing that is useful independently of the porcelain
Repository methods.

The helpers here intentionally operate on pygit's SHA-256 object database and
its loose/packed reference namespace. They do not assume native Git's SHA-1
object size.
"""

from __future__ import annotations

from collections import deque
from typing import Dict, List, Sequence, Set, Tuple

from .objects import CommitObject, TagObject
from .packed_refs import read_packed_refs
from .repo import Repository


_HEX = frozenset("0123456789abcdef")


def _is_oid(value: str) -> bool:
    return len(value) == 64 and all(ch in _HEX for ch in value.lower())


def _resolve_object_id(repo: Repository, name: str) -> str:
    """Resolve a ref name or SHA prefix without requiring a commit object."""
    oid = repo.refs.resolve(name)
    if oid:
        return oid
    oid = repo.store.resolve_prefix(name)
    if oid:
        return oid
    raise KeyError(f"Unknown revision: {name!r}")


def _peel_to_commit(repo: Repository, oid: str, display: str) -> str:
    """Peel annotated tags until *oid* names a commit."""
    current = oid
    seen: Set[str] = set()
    while True:
        if current in seen:
            raise RuntimeError(f"Tag cycle while resolving {display!r}")
        seen.add(current)

        obj = repo.store.read(current)
        if isinstance(obj, CommitObject):
            return current
        if isinstance(obj, TagObject):
            current = obj.target_sha
            continue
        raise RuntimeError(f"Revision {display!r} does not name a commit")


def _commit(repo: Repository, oid: str) -> CommitObject:
    obj = repo.store.read(oid)
    if not isinstance(obj, CommitObject):
        raise RuntimeError(f"Object {oid} in commit ancestry is not a commit")
    return obj


def resolve_commit(repo: Repository, revision: str) -> str:
    """Resolve a commit-ish revision and peel annotated tags."""
    split_at = len(revision)
    for marker in ("~", "^"):
        pos = revision.find(marker)
        if pos >= 0:
            split_at = min(split_at, pos)
    base = revision[:split_at]
    suffix = revision[split_at:]
    if not base:
        raise ValueError(f"Invalid revision: {revision!r}")

    sha = _peel_to_commit(repo, _resolve_object_id(repo, base), revision)

    while suffix:
        operator = suffix[0]
        if operator not in {"~", "^"}:
            raise ValueError(f"Invalid revision suffix in {revision!r}")
        suffix = suffix[1:]

        digits = []
        while suffix and suffix[0].isdigit():
            digits.append(suffix[0])
            suffix = suffix[1:]
        number = int("".join(digits)) if digits else 1

        if operator == "~":
            for _ in range(number):
                commit = _commit(repo, sha)
                if not commit.parents:
                    raise ValueError(f"Revision {revision!r} walks past a root commit")
                sha = commit.parents[0]
        else:
            if number == 0:
                continue
            commit = _commit(repo, sha)
            if number > len(commit.parents):
                raise ValueError(
                    f"Revision {revision!r} requests parent {number}, "
                    f"but commit has {len(commit.parents)} parent(s)"
                )
            sha = commit.parents[number - 1]

    return sha


def _shallow_boundaries(repo: Repository) -> Set[str]:
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

        for parent in _commit(repo, sha).parents:
            queue.append((parent, distance + 1))

    return distances


def merge_bases(repo: Repository, left: str, right: str) -> List[str]:
    """Return all best common ancestors of two revisions."""
    left_sha = resolve_commit(repo, left)
    right_sha = resolve_commit(repo, right)
    left_dist = ancestor_distances(repo, left_sha)
    right_dist = ancestor_distances(repo, right_sha)
    common = set(left_dist).intersection(right_dist)
    if not common:
        return []

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
    ancestor_sha = resolve_commit(repo, ancestor)
    descendant_sha = resolve_commit(repo, descendant)
    return ancestor_sha in ancestor_distances(repo, descendant_sha)


def _matches_ref_pattern(refname: str, pattern: str) -> bool:
    pattern = pattern.strip("/")
    return bool(pattern) and (
        refname == pattern or refname.endswith("/" + pattern)
    )


def _all_refnames(repo: Repository) -> Set[str]:
    names = set(read_packed_refs(repo.pygit_dir))
    refs_root = repo.pygit_dir / "refs"
    if refs_root.exists():
        names.update(
            "refs/" + path.relative_to(refs_root).as_posix()
            for path in refs_root.rglob("*")
            if path.is_file()
        )
    return names


def list_refs(
    repo: Repository,
    *,
    include_head: bool = False,
    heads: bool = False,
    tags: bool = False,
    patterns: Sequence[str] = (),
) -> List[Tuple[str, str]]:
    """Return ``(oid, refname)`` pairs across loose and packed storage.

    Loose direct or symbolic refs shadow packed entries of the same name. Broken
    symbolic refs are omitted; malformed direct or packed refs fail loudly.
    """
    result: List[Tuple[str, str]] = []

    if include_head:
        head = repo.refs.resolve_head()
        if head and (not patterns or any(_matches_ref_pattern("HEAD", p) for p in patterns)):
            result.append((head, "HEAD"))

    selected_namespaces: Set[str] = set()
    if heads:
        selected_namespaces.add("heads")
    if tags:
        selected_namespaces.add("tags")

    for refname in sorted(_all_refnames(repo)):
        relative = refname[len("refs/") :]
        namespace = relative.split("/", 1)[0]
        if selected_namespaces and namespace not in selected_namespaces:
            continue
        if patterns and not any(_matches_ref_pattern(refname, pattern) for pattern in patterns):
            continue

        oid = repo.refs.resolve(refname)
        if oid is None:
            continue
        if not _is_oid(oid):
            raise RuntimeError(f"Malformed ref {refname}: expected a 64-hex object ID")
        result.append((oid.lower(), refname))

    return result


def verify_ref(repo: Repository, refname: str) -> Tuple[str, str]:
    """Resolve one exact, fully-qualified loose or packed ref name."""
    if not refname.startswith("refs/"):
        raise ValueError("--verify requires an exact ref name beginning with 'refs/'")

    # Force traversal validation through RefStore even when a malicious path does
    # not currently exist.
    relative = refname[len("refs/") :]
    repo.refs._path_under(repo.pygit_dir / "refs", relative)

    if refname not in _all_refnames(repo):
        raise KeyError(refname)
    oid = repo.refs.resolve(refname)
    if oid is None:
        raise KeyError(refname)
    if not _is_oid(oid):
        raise RuntimeError(f"Malformed ref {refname}: expected a 64-hex object ID")
    return oid.lower(), refname


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
