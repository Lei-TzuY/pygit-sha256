"""Reflog-aware fork-point discovery for ``merge-base --fork-point``.

Git's fork-point mode differs from an ordinary merge-base: the candidate base
must have been the tip of the supplied reference, either now or in its reflog.
This avoids silently choosing an older common ancestor after an upstream ref was
rewound and rebuilt.
"""

from __future__ import annotations

from typing import List, Optional, Set

from .graph_query import merge_bases_many
from .plumbing import resolve_commit
from .reflog_show import show_reflog
from .repo import Repository
from .revision import symbolic_refname


_ZERO_OID = "0" * 64


def _canonical_ref(repo: Repository, ref: str) -> str:
    """Return the exact ref whose reflog should participate in fork-point."""

    if ref == "HEAD":
        resolve_commit(repo, ref)
        return "HEAD"
    canonical = symbolic_refname(repo, ref)
    if canonical is None:
        raise ValueError(
            "merge-base --fork-point requires a reference as its first argument"
        )
    return canonical


def _reflog_tip_candidates(repo: Repository, ref: str, canonical_ref: str) -> List[str]:
    """Return distinct commit tips represented by the ref and its reflog.

    Each reflog ``new_oid`` is one value the ref actually held. The current
    resolved tip is included independently so fork-point still works when no
    reflog exists. An oldest retained record's ``old_oid`` is deliberately not
    added: once a tip no longer appears as a retained reflog value, Git's
    fork-point semantics require that candidate to be considered expired.
    """

    raw_oids = [resolve_commit(repo, ref)]
    raw_oids.extend(entry.new_oid for entry in show_reflog(repo, canonical_ref))

    candidates: List[str] = []
    seen: Set[str] = set()
    for raw_oid in raw_oids:
        if raw_oid == _ZERO_OID:
            continue
        oid = resolve_commit(repo, raw_oid)
        if oid in seen:
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
    If reflog expiry removed the relevant historical tip, ``None`` is returned
    rather than falling back to an older ordinary merge-base.
    """

    canonical_ref = _canonical_ref(repo, ref)
    derived = resolve_commit(repo, commit)
    candidates = _reflog_tip_candidates(repo, ref, canonical_ref)

    bases = merge_bases_many(repo, [derived, *candidates])
    if len(bases) != 1:
        return None
    base = bases[0]
    return base if base in set(candidates) else None
