"""Conservative repository maintenance orchestration for ``pygit gc``.

Phase 74 composes the independently hardened maintenance primitives added in
Phases 71-73.  Every destructive phase is preflighted before the first real
mutation, and recovery metadata is never expired in the same pass that its
historical objects become eligible for deletion.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional, Set, Tuple

from .fsck import fsck
from .prune import PruneResult, default_expire_before, prune
from .reflog_expire import (
    ReflogExpireResult,
    default_reflog_expire_before,
    default_reflog_unreachable_before,
    expire_reflogs,
)
from .repack import RepackResult, repack
from .repo import Repository


_ZERO_OID = "0" * 64


@dataclass(frozen=True)
class GarbageCollectResult:
    """Summary of one coordinated garbage-collection pass."""

    preflight_reachable: int
    repack: RepackResult
    reflog: Optional[ReflogExpireResult]
    prune: Optional[PruneResult]
    final_reachable: int
    preserved_expired_roots: Tuple[str, ...]
    dry_run: bool


def _healthy(repo: Repository, *, stage: str):
    report = fsck(repo)
    if report.errors:
        raise RuntimeError(
            f"cannot {stage} an unhealthy repository: {report.errors[0].render()}"
        )
    return report


def _expired_roots(result: Optional[ReflogExpireResult]) -> Tuple[str, ...]:
    """Return non-zero OIDs named by reflog records expired in this pass."""
    if result is None:
        return ()
    roots: Set[str] = set()
    for entry in result.entries:
        for oid in (entry.old_oid, entry.new_oid):
            if oid != _ZERO_OID:
                roots.add(oid)
    return tuple(sorted(roots))


def garbage_collect(
    repo: Repository,
    *,
    prune_expire_before: Optional[float] = None,
    reflog_expire_before: Optional[float] = None,
    reflog_unreachable_before: Optional[float] = None,
    expire_reflogs_enabled: bool = True,
    prune_objects: bool = True,
    dry_run: bool = False,
) -> GarbageCollectResult:
    """Run verified pack, reflog, and loose-object maintenance as one operation.

    The policy clock is frozen once at the beginning so preflight and execution
    use identical cutoffs.  Full ``fsck`` runs first, then every requested phase
    is dry-planned before any mutation occurs.  Execution order is:

    1. verified full reachable-object repack and redundant-pack cleanup;
    2. atomic reflog expiry across every log; and
    3. grace-aware loose-object pruning.

    OIDs mentioned by reflog records expired during this same invocation are
    passed to :func:`pygit.prune.prune` as extra retention roots.  Removing a
    recovery record therefore cannot delete the corresponding historical object
    graph in the same command.  A later gc may reclaim it once no recovery root
    remains and the normal prune age policy permits removal.

    ``dry_run=True`` never writes repository state.  Because the planned expired
    roots are also supplied to prune preflight, dry-run and execution share the
    same conservative recovery boundary.
    """

    now = time.time()
    prune_cutoff = (
        default_expire_before(now)
        if prune_expire_before is None
        else float(prune_expire_before)
    )
    reflog_cutoff = (
        default_reflog_expire_before(now)
        if reflog_expire_before is None
        else float(reflog_expire_before)
    )
    unreachable_cutoff = (
        default_reflog_unreachable_before(now)
        if reflog_unreachable_before is None
        else float(reflog_unreachable_before)
    )

    initial = _healthy(repo, stage="garbage-collect")

    # Preflight the complete requested pipeline before the first mutation.
    repack_plan = repack(
        repo,
        all_objects=True,
        delete_redundant=True,
        dry_run=True,
    )
    reflog_plan: Optional[ReflogExpireResult] = None
    if expire_reflogs_enabled:
        reflog_plan = expire_reflogs(
            repo,
            all_refs=True,
            expire_before=reflog_cutoff,
            expire_unreachable_before=unreachable_cutoff,
            dry_run=True,
        )
    planned_roots = _expired_roots(reflog_plan)

    prune_plan: Optional[PruneResult] = None
    if prune_objects:
        prune_plan = prune(
            repo,
            expire_before=prune_cutoff,
            dry_run=True,
            extra_heads=planned_roots,
        )

    if dry_run:
        return GarbageCollectResult(
            preflight_reachable=len(initial.reachable),
            repack=repack_plan,
            reflog=reflog_plan,
            prune=prune_plan,
            final_reachable=len(initial.reachable),
            preserved_expired_roots=planned_roots,
            dry_run=True,
        )

    # Repack first: a storage-write failure must not shorten recovery metadata.
    repack_result = repack(
        repo,
        all_objects=True,
        delete_redundant=True,
        dry_run=False,
    )

    reflog_result: Optional[ReflogExpireResult] = None
    if expire_reflogs_enabled:
        reflog_result = expire_reflogs(
            repo,
            all_refs=True,
            expire_before=reflog_cutoff,
            expire_unreachable_before=unreachable_cutoff,
            dry_run=False,
        )
    actual_roots = _expired_roots(reflog_result)

    prune_result: Optional[PruneResult] = None
    if prune_objects:
        prune_result = prune(
            repo,
            expire_before=prune_cutoff,
            dry_run=False,
            extra_heads=actual_roots,
        )

    final = _healthy(repo, stage="finish garbage-collecting")
    return GarbageCollectResult(
        preflight_reachable=len(initial.reachable),
        repack=repack_result,
        reflog=reflog_result,
        prune=prune_result,
        final_reachable=len(final.reachable),
        preserved_expired_roots=actual_roots,
        dry_run=False,
    )
