"""Safe repository maintenance orchestration for ``pygit gc``.

The legacy high-level ``Repository.gc`` predates the repository's later safety
primitives and immediately deletes dangling loose objects.  This module is the
script-facing maintenance path: validate first, compact verified storage, expire
reflogs atomically, and only then run grace-aware loose-object pruning.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Set, Tuple

from .fsck import fsck
from .prune import PruneResult, prune
from .reflog_expire import ReflogExpireResult, expire_reflogs as _expire_reflogs
from .repack import RepackResult, repack
from .repo import Repository


_ZERO_OID = "0" * 64


@dataclass(frozen=True)
class GarbageCollectResult:
    """Summary of one garbage-collection maintenance pass."""

    checked_objects: int
    reachable: int
    reflog: Optional[ReflogExpireResult]
    repack: RepackResult
    prune: Optional[PruneResult]
    preserved_expired_roots: Tuple[str, ...]
    dry_run: bool

    @property
    def expired_reflog_entries(self) -> int:
        return self.reflog.expired if self.reflog is not None else 0

    @property
    def pruned_objects(self) -> int:
        return self.prune.pruned if self.prune is not None else 0


def _expired_roots(result: Optional[ReflogExpireResult]) -> Tuple[str, ...]:
    """Return non-zero OIDs mentioned by records expired in this pass.

    These OIDs remain explicit prune roots for the rest of the current gc run.
    Reflog expiry therefore closes the metadata recovery window now, while the
    corresponding objects survive until a later maintenance cycle.
    """
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
    prune_objects: bool = True,
    expire_reflogs: bool = True,
    prune_before: Optional[float] = None,
    reflog_expire_before: Optional[float] = None,
    reflog_expire_unreachable_before: Optional[float] = None,
    dry_run: bool = False,
) -> GarbageCollectResult:
    """Run conservative repository maintenance.

    The operation has an explicit preflight phase.  Full ``fsck``, reflog
    parsing/expiry planning, repack planning, and prune planning must all succeed
    before the first repository mutation.  Actual maintenance then proceeds in
    the safest useful order: verified full repack, atomic reflog expiry, and
    grace-aware loose-object pruning.

    Objects named by reflog entries expired during this same invocation are
    passed to :func:`pygit.prune.prune` as extra retention roots.  They therefore
    cannot disappear in the same command that removes their recovery metadata.
    A later gc may reclaim them once no remaining recovery root retains them and
    the normal prune age policy allows removal.
    """
    report = fsck(repo)
    if report.errors:
        raise RuntimeError(
            "cannot garbage-collect an unhealthy repository: "
            + report.errors[0].render()
        )

    # Preflight every stage before the first mutation.  Besides improving error
    # locality, this prevents malformed reflogs or missing historical roots from
    # being discovered only after a new pack has already been installed.
    reflog_plan: Optional[ReflogExpireResult] = None
    if expire_reflogs:
        reflog_plan = _expire_reflogs(
            repo,
            all_refs=True,
            expire_before=reflog_expire_before,
            expire_unreachable_before=reflog_expire_unreachable_before,
            dry_run=True,
        )
    planned_roots = _expired_roots(reflog_plan)

    repack_plan = repack(
        repo,
        all_objects=True,
        delete_redundant=True,
        dry_run=True,
    )

    prune_plan: Optional[PruneResult] = None
    if prune_objects:
        prune_plan = prune(
            repo,
            expire_before=prune_before,
            dry_run=True,
            extra_heads=planned_roots,
        )

    if dry_run:
        return GarbageCollectResult(
            checked_objects=len(report.checked_objects),
            reachable=len(report.reachable),
            reflog=reflog_plan,
            repack=repack_plan,
            prune=prune_plan,
            preserved_expired_roots=planned_roots,
            dry_run=True,
        )

    # A verified pack rewrite happens first.  If it fails, recovery metadata is
    # untouched.  Repack itself validates the generated pair before cleanup.
    repack_result = repack(
        repo,
        all_objects=True,
        delete_redundant=True,
        dry_run=False,
    )

    reflog_result: Optional[ReflogExpireResult] = None
    if expire_reflogs:
        reflog_result = _expire_reflogs(
            repo,
            all_refs=True,
            expire_before=reflog_expire_before,
            expire_unreachable_before=reflog_expire_unreachable_before,
            dry_run=False,
        )
    actual_roots = _expired_roots(reflog_result)

    prune_result: Optional[PruneResult] = None
    if prune_objects:
        prune_result = prune(
            repo,
            expire_before=prune_before,
            dry_run=False,
            extra_heads=actual_roots,
        )

    return GarbageCollectResult(
        checked_objects=len(report.checked_objects),
        reachable=len(report.reachable),
        reflog=reflog_result,
        repack=repack_result,
        prune=prune_result,
        preserved_expired_roots=actual_roots,
        dry_run=False,
    )
