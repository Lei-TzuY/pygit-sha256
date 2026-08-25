"""Conservative repository maintenance orchestration for ``pygit gc``.

Phase 74 composes the independently hardened maintenance primitives added in
Phases 71-73.  The important property is ordering: every destructive phase is
first exercised in dry-run/preflight mode, so malformed reflogs, corrupt pack
pairs, or unhealthy object connectivity are discovered before the first real
mutation.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

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


@dataclass(frozen=True)
class GarbageCollectResult:
    """Summary of one coordinated garbage-collection pass."""

    preflight_reachable: int
    repack: RepackResult
    reflog: Optional[ReflogExpireResult]
    prune: Optional[PruneResult]
    final_reachable: int
    dry_run: bool


def _healthy(repo: Repository, *, stage: str):
    report = fsck(repo)
    if report.errors:
        raise RuntimeError(
            f"cannot {stage} an unhealthy repository: {report.errors[0].render()}"
        )
    return report


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
    use exactly the same cutoffs.  A full fsck is performed first.  Then every
    requested phase is dry-planned before any mutation occurs:

    1. full reachable-object repack with redundant-pack cleanup,
    2. reflog expiry across every log,
    3. conservative loose-object pruning.

    Execution uses the same order.  Repacking happens before reflog expiry so
    the current reachable graph gains a verified packed copy before recovery
    records are shortened.  A final full fsck verifies the resulting storage.

    ``dry_run=True`` never writes repository state.  Its prune sub-result is
    intentionally conservative because reflogs are not actually rewritten in a
    dry run; objects protected by records that *would* expire can therefore be
    absent from the dry-run prune candidate list.
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
    prune_plan: Optional[PruneResult] = None
    if prune_objects:
        prune_plan = prune(
            repo,
            expire_before=prune_cutoff,
            dry_run=True,
        )

    if dry_run:
        return GarbageCollectResult(
            preflight_reachable=len(initial.reachable),
            repack=repack_plan,
            reflog=reflog_plan,
            prune=prune_plan,
            final_reachable=len(initial.reachable),
            dry_run=True,
        )

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

    prune_result: Optional[PruneResult] = None
    if prune_objects:
        prune_result = prune(
            repo,
            expire_before=prune_cutoff,
            dry_run=False,
        )

    final = _healthy(repo, stage="finish garbage-collecting")
    return GarbageCollectResult(
        preflight_reachable=len(initial.reachable),
        repack=repack_result,
        reflog=reflog_result,
        prune=prune_result,
        final_reachable=len(final.reachable),
        dry_run=False,
    )
