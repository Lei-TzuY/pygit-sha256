"""Reflog-aware fork-point discovery for ``merge-base --fork-point``.

Git's fork-point mode differs from an ordinary merge-base: the candidate base
must have been the tip of the supplied reference, either now or in its reflog.
This avoids silently choosing an older common ancestor after an upstream ref was
rewound and rebuilt.
"""

from __future__ import annotations

from typing import List, Optional, Set

from .graph_query import merge_bases_many
from .objects import CommitObject, TagObject
from .reflog_show import show_reflog
from .repo import Repository
from .revision import resolve_revision, symbolic_refname


_ZERO_OID = "0" * 64


def _canonical_ref(repo: Repository, ref: str) -> str:
    """Return the exact ref whose reflog should participate in fork-point."""

    if ref == "HEAD":
        _commit_oid(repo, ref)
        return "HEAD"
    canonical = symbolic_refname(repo, ref)
    if canonical is None:
        raise ValueError(
            "merge-base --fork-point requires a reference as its first argument"
        )
    return canonical


def _commit_oid(repo: Repository, revision: str) -> str:
    """Resolve through the shared revision grammar and peel tags to a commit."""

    current = resolve_revision(repo, revision)
    seen: Set[str] = set()
    while True:
        if current in seen:
            raise RuntimeError(f"Tag cycle while resolving {revision!r}")
        seen.add(current)
        obj = repo.store.read(current)
        if isinstance(obj, CommitObject):
            return current
        if isinstance(obj, TagObject):
            current = obj.target_sha.lower()
            continue
        raise RuntimeError(f"Revision {revision!r} does not name a commit")


def _historical_commit(repo: Repository, raw_oid: str, ref: str) -> Optional[str]:
    """Peel a retained reflog tip, tolerating history already pruned away."""

    current = raw_oid.lower()
    if current == _ZERO_OID or not repo.store.exists(current):
        return None

    seen: Set[str] = set()
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


def _reflog_tip_candidates(repo: Repository, ref: str, canonical_ref: str) -> List[str]:
    """Return distinct commit tips represented by the ref and its reflog.

    Each reflog ``new_oid`` is one value the ref actually held. The current
    resolved tip is included independently so fork-point still works when no
    reflog exists. An oldest retained record's ``old_oid`` is deliberately not
    added: once a tip no longer appears as a retained reflog value, Git's
    fork-point semantics require that candidate to be considered expired.

    A reflog can outlive an individual historical object after pruning. Missing
    historical tips are ignored as unavailable evidence; existing non-commit
    tips still fail loudly.
    """

    candidates: List[str] = [_commit_oid(repo, ref)]
    seen: Set[str] = set(candidates)
    for entry in show_reflog(repo, canonical_ref):
        oid = _historical_commit(repo, entry.new_oid, canonical_ref)
        if oid is None or oid in seen:
            continue
        seen.add(oid)
        candidates.append(oid)
    return candidates


def fork_point(
    repo: Repository,
    ref: str,
    commit: str = "HEAD",
) -> Optional[str]:
    """Find the reflog-aware fork point of *commit* from *ref*.

    The merge-base is computed between ``commit`` and a hypothetical merge of
    every current/historical tip of ``ref``. A result is accepted only when
    there is exactly one best base and that base is itself one of those tips.
    If reflog expiry/pruning removed the relevant historical tip, ``None`` is
    returned rather than falling back to an older ordinary merge-base.
    """

    canonical_ref = _canonical_ref(repo, ref)
    derived = _commit_oid(repo, commit)
    candidates = _reflog_tip_candidates(repo, ref, canonical_ref)

    # The graph helper receives full 64-hex commit IDs. Public arguments above
    # still benefit from the modern shared revision grammar while the existing
    # Phase 52 graph implementation remains unchanged.
    bases = merge_bases_many(repo, [derived, *candidates])
    if len(bases) != 1:
        return None
    base = bases[0]
    return base if base in set(candidates) else None
