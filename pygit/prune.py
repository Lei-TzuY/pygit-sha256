"""Safe unreachable loose-object pruning.

The command intentionally prunes only canonical loose objects.  Current refs,
the index, shallow boundaries, reflog history, and caller-supplied heads are all
retention roots.  A connectivity failure aborts the entire operation before any
unlink, while recent or malformed loose objects are preserved.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Set, Tuple

from .fsck import fsck
from .pack_objects import reachable_objects
from .prune_packed import _loose_objects, _valid_loose_copy
from .repo import Repository
from .revision import resolve_revision


_HEX = frozenset("0123456789abcdef")
_ZERO_OID = "0" * 64
_DEFAULT_GRACE_SECONDS = 14 * 24 * 60 * 60


@dataclass(frozen=True)
class PruneResult:
    """Summary of one unreachable loose-object prune pass."""

    scanned_loose: int
    reachable: int
    reflog_roots: int
    candidates: int
    kept_recent: Tuple[str, ...]
    skipped_loose: Tuple[str, ...]
    oids: Tuple[str, ...]
    pruned: int
    expire_before: float
    dry_run: bool


def _is_oid(value: str) -> bool:
    return len(value) == 64 and all(char in _HEX for char in value.lower())


def _reflog_roots(repo: Repository) -> Set[str]:
    """Strictly parse every reflog and return all non-zero old/new object IDs."""
    logs = repo.pygit_dir / "logs"
    if not logs.exists():
        return set()

    roots: Set[str] = set()
    for path in sorted(item for item in logs.rglob("*") if item.is_file()):
        relative = path.relative_to(repo.pygit_dir).as_posix()
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            metadata, separator, _ = line.partition("\t")
            parts = metadata.split()
            if not separator or len(parts) < 2:
                raise ValueError(f"malformed reflog record {relative}:{lineno}")
            for value in parts[:2]:
                oid = value.lower()
                if not _is_oid(oid):
                    raise ValueError(
                        f"malformed reflog object ID at {relative}:{lineno}"
                    )
                if oid != _ZERO_OID:
                    roots.add(oid)
    return roots


def _retained_objects(repo: Repository, extra_heads: Iterable[str]) -> tuple[Set[str], Set[str]]:
    """Return the full retention closure and the reflog root set."""
    report = fsck(repo, connectivity_only=True)
    if report.errors:
        first = report.errors[0].render()
        raise RuntimeError(f"cannot prune an unhealthy repository: {first}")

    retained = set(report.reachable)
    reflog_roots = _reflog_roots(repo)
    extra_roots = {resolve_revision(repo, expression) for expression in extra_heads}
    historical_roots = reflog_roots | extra_roots
    if historical_roots:
        retained.update(reachable_objects(repo, historical_roots))
    return retained, reflog_roots


def default_expire_before(now: Optional[float] = None) -> float:
    """Return the conservative default cutoff: two weeks before *now*."""
    current = time.time() if now is None else float(now)
    return current - _DEFAULT_GRACE_SECONDS


def prune(
    repo: Repository,
    *,
    expire_before: Optional[float] = None,
    dry_run: bool = False,
    extra_heads: Iterable[str] = (),
) -> PruneResult:
    """Remove expired unreachable loose objects while preserving recovery roots.

    Only ``objects/aa/<62hex>`` loose paths are considered.  Every deletion
    candidate is validated before the first unlink.  Packed objects are never
    removed by this command; use pack-specific maintenance for those files.
    """
    cutoff = default_expire_before() if expire_before is None else float(expire_before)
    retained, reflog_roots = _retained_objects(repo, extra_heads)
    loose = _loose_objects(repo)
    candidate_oids = sorted(set(loose) - retained)

    expired: List[tuple[str, Path]] = []
    recent: List[str] = []
    skipped: List[str] = []

    # Validate all candidates before the first destructive operation.
    for oid in candidate_oids:
        path = loose[oid]
        if not _valid_loose_copy(path, oid):
            skipped.append(oid)
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            skipped.append(oid)
            continue
        if mtime > cutoff:
            recent.append(oid)
        else:
            expired.append((oid, path))

    if not dry_run:
        for _, path in expired:
            path.unlink()
        for directory in sorted({path.parent for _, path in expired}):
            try:
                directory.rmdir()
            except OSError:
                pass

    oids = tuple(oid for oid, _ in expired)
    return PruneResult(
        scanned_loose=len(loose),
        reachable=len(retained),
        reflog_roots=len(reflog_roots),
        candidates=len(candidate_oids),
        kept_recent=tuple(recent),
        skipped_loose=tuple(skipped),
        oids=oids,
        pruned=0 if dry_run else len(expired),
        expire_before=cutoff,
        dry_run=dry_run,
    )
