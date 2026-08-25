"""Reflog-aware fork-point discovery for ``merge-base --fork-point``."""

from __future__ import annotations

from typing import Optional, Set

from .objects import CommitObject, TagObject
from .plumbing import ancestor_distances
from .reflog_show import show_reflog
from .repo import Repository
from .revision import resolve_revision, symbolic_refname


_ZERO_OID = "0" * 64


def _commit_oid(repo: Repository, revision: str) -> str:
    """Resolve *revision* through the shared resolver and peel tags to a commit."""
    oid = resolve_revision(repo, revision)
    seen: Set[str] = set()
    while True:
        if oid in seen:
            raise RuntimeError(f"Tag cycle while resolving {revision!r}")
        seen.add(oid)
        obj = repo.store.read(oid)
        if isinstance(obj, CommitObject):
            return oid
        if isinstance(obj, TagObject):
            oid = obj.target_sha.lower()
            continue
        raise RuntimeError(f"Revision {revision!r} does not name a commit")


def _fork_refname(repo: Repository, ref: str) -> str:
    """Return the concrete reflog name used as fork-point history."""
    if ref == "HEAD":
        return "HEAD"
    symbolic = symbolic_refname(repo, ref)
    if symbolic is None:
        raise KeyError(f"fork-point requires an existing ref: {ref!r}")
    return symbolic


def _historical_commit(repo: Repository, oid: str, ref: str) -> Optional[str]:
    """Return one usable historical commit, ignoring expired/pruned object IDs."""
    oid = oid.lower()
    if oid == _ZERO_OID or not repo.store.exists(oid):
        return None

    seen: Set[str] = set()
    current = oid
    while True:
        if current in seen:
            raise RuntimeError(f"Tag cycle in reflog history for {ref!r}")
        seen.add(current)
        obj = repo.store.read(current)
        if isinstance(obj, CommitObject):
            return current
        if isinstance(obj, TagObject):
            current = obj.target_sha.lower()
            if not repo.store.exists(current):
                return None
            continue
        raise RuntimeError(
            f"Reflog for {ref!r} contains non-commit object {current}"
        )


def fork_point(
    repo: Repository,
    ref: str,
    commit: str = "HEAD",
) -> Optional[str]:
    """Return the unique best historical tip of *ref* reachable from *commit*.

    The reference must currently exist and have a reflog.  Current and
    historical ref states are intersected with the ancestry of *commit*;
    candidates dominated by a newer eligible historical state are removed.
    ``None`` means that no unique fork point can be established from the
    available reflog history.
    """
    reflog_ref = _fork_refname(repo, ref)
    current_ref = _commit_oid(repo, ref)
    derived = _commit_oid(repo, commit)

    entries = show_reflog(repo, reflog_ref)
    if not entries:
        return None

    raw_candidates = [current_ref]
    for entry in entries:
        raw_candidates.append(entry.new_oid)
        raw_candidates.append(entry.old_oid)

    candidates: Set[str] = set()
    for oid in raw_candidates:
        candidate = _historical_commit(repo, oid, reflog_ref)
        if candidate is not None:
            candidates.add(candidate)

    if not candidates:
        return None

    derived_ancestry = ancestor_distances(repo, derived)
    eligible = candidates.intersection(derived_ancestry)
    if not eligible:
        return None

    dominated: Set[str] = set()
    for candidate in eligible:
        candidate_ancestry = ancestor_distances(repo, candidate)
        dominated.update((eligible.intersection(candidate_ancestry)) - {candidate})

    best = eligible - dominated
    if len(best) != 1:
        return None
    return next(iter(best))
