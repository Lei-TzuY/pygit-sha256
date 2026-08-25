"""Human-readable commit naming built from refs and ancestry paths.

This module implements a focused ``git name-rev`` style query for pygit's
SHA-256 commit graph.  It is intentionally read-only: refs, the index and the
working tree are never modified.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import fnmatch
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .objects import CommitObject, TagObject
from .plumbing import peel_oid, resolve_commit
from .repo import Repository


_HEX = frozenset("0123456789abcdef")


@dataclass(frozen=True)
class NameRevResult:
    revision: str
    oid: str
    name: Optional[str]


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


def _display_name(refname: str, annotated: bool) -> Tuple[str, int]:
    if refname.startswith("refs/tags/"):
        base = "tags/" + refname[len("refs/tags/") :]
        if annotated:
            base += "^0"
        return base, 0
    if refname.startswith("refs/heads/"):
        return refname[len("refs/heads/") :], 1
    if refname.startswith("refs/remotes/"):
        return "remotes/" + refname[len("refs/remotes/") :], 2
    return refname, 3


def _matches_patterns(refname: str, display: str, patterns: Sequence[str]) -> bool:
    if not patterns:
        return True
    return any(
        fnmatch.fnmatchcase(refname, pattern)
        or fnmatch.fnmatchcase(display, pattern)
        for pattern in patterns
    )


def _read_ref_oid(repo: Repository, refname: str, seen: Optional[Set[str]] = None) -> Optional[str]:
    """Read a direct or symbolic fully-qualified ref with cycle protection."""
    if not refname.startswith("refs/"):
        return None
    seen = set() if seen is None else seen
    if refname in seen or len(seen) >= 32:
        return None
    seen.add(refname)

    root = (repo.pygit_dir / "refs").resolve()
    path = (repo.pygit_dir / refname).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    if not path.is_file():
        return None

    raw = path.read_text(encoding="utf-8").strip()
    if raw.startswith("ref: "):
        return _read_ref_oid(repo, raw[5:].strip(), seen)
    if not _is_oid(raw):
        return None
    return raw.lower()


def _physical_refs(repo: Repository) -> Iterable[Tuple[str, str, bool]]:
    """Yield ``(refname, commit_oid, annotated_tag)`` records.

    Symbolic refs under ``refs/`` are dereferenced with bounded cycle detection.
    Broken refs, refs to non-commit objects and malformed object IDs are ignored
    so a naming query remains useful when unrelated namespaces contain special
    or damaged refs.
    """
    root = repo.pygit_dir / "refs"
    if not root.exists():
        return

    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        refname = "refs/" + path.relative_to(root).as_posix()
        oid = _read_ref_oid(repo, refname)
        if not oid:
            continue

        try:
            obj = repo.store.read(oid)
            annotated = isinstance(obj, TagObject)
            peeled = peel_oid(repo, oid)
            if not isinstance(repo.store.read(peeled), CommitObject):
                continue
        except (KeyError, ValueError, RuntimeError):
            continue
        yield refname, peeled, annotated


def _format_path(base: str, parents: Sequence[int]) -> str:
    if not parents:
        return base

    result = base
    first_parent_run = 0

    def flush() -> None:
        nonlocal result, first_parent_run
        if first_parent_run:
            result += f"~{first_parent_run}"
            first_parent_run = 0

    for index in parents:
        if index == 0:
            first_parent_run += 1
            continue
        flush()
        result += f"^{index + 1}"
    flush()
    return result


def _candidate_names(
    repo: Repository,
    *,
    tags_only: bool = False,
    ref_patterns: Sequence[str] = (),
) -> Dict[str, str]:
    """Return the best human-readable name for every reachable commit."""
    shallow = _shallow_boundaries(repo)
    best: Dict[str, Tuple[Tuple[int, int, int, str], str]] = {}

    for refname, tip, annotated in _physical_refs(repo):
        if tags_only and not refname.startswith("refs/tags/"):
            continue
        display, priority = _display_name(refname, annotated)
        if not _matches_patterns(refname, display, ref_patterns):
            continue

        queue = deque([(tip, tuple())])
        seen: Set[str] = set()
        while queue:
            oid, path = queue.popleft()
            if oid in seen:
                continue
            seen.add(oid)

            name = _format_path(display, path)
            rank = (len(path), priority, len(name), name)
            current = best.get(oid)
            if current is None or rank < current[0]:
                best[oid] = (rank, name)

            if oid in shallow:
                continue
            commit = _commit(repo, oid)
            for index, parent in enumerate(commit.parents):
                queue.append((parent, path + (index,)))

    return {oid: value[1] for oid, value in best.items()}


def name_revision(
    repo: Repository,
    revision: str,
    *,
    tags_only: bool = False,
    ref_patterns: Sequence[str] = (),
) -> NameRevResult:
    oid = resolve_commit(repo, revision)
    names = _candidate_names(repo, tags_only=tags_only, ref_patterns=ref_patterns)
    return NameRevResult(revision, oid, names.get(oid))


def name_revisions(
    repo: Repository,
    revisions: Sequence[str],
    *,
    tags_only: bool = False,
    ref_patterns: Sequence[str] = (),
) -> List[NameRevResult]:
    names = _candidate_names(repo, tags_only=tags_only, ref_patterns=ref_patterns)
    result: List[NameRevResult] = []
    for revision in revisions:
        oid = resolve_commit(repo, revision)
        result.append(NameRevResult(revision, oid, names.get(oid)))
    return result


def name_all(
    repo: Repository,
    *,
    tags_only: bool = False,
    ref_patterns: Sequence[str] = (),
) -> List[NameRevResult]:
    names = _candidate_names(repo, tags_only=tags_only, ref_patterns=ref_patterns)
    records = [NameRevResult(oid, oid, name) for oid, name in names.items()]
    records.sort(
        key=lambda record: (
            -_commit(repo, record.oid).committer.timestamp,
            record.oid,
        )
    )
    return records


def abbreviated_oid(oid: str, width: int = 12) -> str:
    if width <= 0:
        raise ValueError("abbreviation width must be positive")
    return oid[: min(width, len(oid))]
