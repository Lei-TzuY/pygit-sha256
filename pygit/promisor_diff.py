"""Promisor-aware batching for diff operations.

Git partial-clone repositories may intentionally omit blobs until an operation
needs their contents.  pygit's historical ``Repository.diff`` implementation
flattens commit trees before rendering a diff; unresolved foreign tree entries
therefore used to fault in one blob at a time.

Phase225 predicts the complete commit snapshots already known to be required by
the requested diff mode and materializes their unresolved promises in one batch
before the historical renderer starts consuming ``TreeEntry.sha`` values.
Working-tree-only diffs stay on the original path because their index objects are
already local SHA-256 objects.
"""

from __future__ import annotations

from functools import wraps
from typing import Iterable, Set, Type

from .promisor import read_promisor_state
from .promisor_checkout import collect_checkout_promises
from .promisor_materialize import materialize_promised_objects


_INSTALLED = False


def collect_diff_promises(repo, commit_shas: Iterable[str]) -> Set[str]:
    """Return the deduplicated unresolved blobs required by commit snapshots."""
    promises: Set[str] = set()
    for commit_sha in commit_shas:
        promises.update(collect_checkout_promises(repo, commit_sha))
    return promises


def install_promisor_diff_support(repository_cls: Type) -> None:
    """Batch partial-clone promises before commit-backed diff modes."""
    global _INSTALLED
    if _INSTALLED:
        return

    original_diff = repository_cls.diff

    @wraps(original_diff)
    def diff(self, *args, **kwargs):
        state = read_promisor_state(self.pygit_dir)
        if not state.get("promised"):
            return original_diff(self, *args, **kwargs)

        # Repository.diff's public positional order starts with cached, stat,
        # from_ref, to_ref.  Normalize only the arguments needed to predict
        # commit snapshots and leave all rendering semantics to the original.
        cached = kwargs.get("cached", args[0] if len(args) > 0 else False)
        from_ref = kwargs.get("from_ref", args[2] if len(args) > 2 else None)
        to_ref = kwargs.get("to_ref", args[3] if len(args) > 3 else None)

        commits = []
        if from_ref is not None:
            commits.append(self._resolve_revision(from_ref))
            if to_ref is not None:
                commits.append(self._resolve_revision(to_ref))
        elif cached:
            head_sha = self.refs.resolve_head()
            if head_sha:
                commits.append(head_sha)

        if commits:
            promises = collect_diff_promises(self, commits)
            if promises:
                materialize_promised_objects(self.pygit_dir, sorted(promises))

        return original_diff(self, *args, **kwargs)

    repository_cls.diff = diff
    _INSTALLED = True
