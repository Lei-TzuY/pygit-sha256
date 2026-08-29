"""Promisor-aware batching for blame history reads.

``Repository.blame`` walks commit history and, for each commit plus its first
parent, flattens complete trees before reading the selected file. In a partial
clone those foreign tree entries may still refer to promised native Git SHA-1
blobs, so the historical implementation can otherwise trigger one demand-fetch
request per missing object.

Phase227 predicts the exact commit snapshots that the existing blame algorithm
will inspect, collects their unresolved promises, and materializes the
deduplicated union before attribution begins. The established blame
implementation remains authoritative for path normalization, empty-history and
missing-worktree errors, line attribution, line ranges, author formatting, and
returned output.
"""

from __future__ import annotations

from functools import wraps
from typing import Set, Type

from .objects import CommitObject
from .promisor import read_promisor_state
from .promisor_history import prefetch_history_promises


_INSTALLED = False


def plan_blame_snapshots(repo, commits) -> Set[str]:
    """Return commit snapshots consumed by the historical blame algorithm."""
    snapshots: Set[str] = set()
    for sha, commit in commits:
        snapshots.add(sha)
        if isinstance(commit, CommitObject) and commit.parents:
            snapshots.add(commit.parents[0])
    return snapshots


def install_promisor_blame_support(repository_cls: Type) -> None:
    """Batch unresolved history objects before ``Repository.blame``."""
    global _INSTALLED
    if _INSTALLED:
        return

    original_blame = repository_cls.blame

    @wraps(original_blame)
    def blame(self, path: str, line_range=None):
        state = read_promisor_state(self.pygit_dir)
        if not state.get("promised"):
            return original_blame(self, path, line_range=line_range)

        # Mirror the historical error ordering without consuming any promised
        # tree entries: blame asks metadata-only log first, then verifies that
        # the selected worktree path exists. Invalid cases must remain network-
        # free and let the original method own the exact exception text.
        commits = self.log()
        if not commits:
            return original_blame(self, path, line_range=line_range)

        rel = self._normalize_pathspec(path)
        if not (self.worktree / rel).exists():
            return original_blame(self, path, line_range=line_range)

        snapshots = plan_blame_snapshots(self, commits)
        if snapshots:
            prefetch_history_promises(self, sorted(snapshots))

        return original_blame(self, path, line_range=line_range)

    repository_cls.blame = blame
    _INSTALLED = True
