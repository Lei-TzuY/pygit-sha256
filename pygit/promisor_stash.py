"""Promisor-aware batching for stash restore operations.

Phase229 closes the partial-clone demand-fetch waterfall in ``stash apply`` and
``stash pop``.  The historical stash implementation validates the requested
entry and worktree cleanliness before flattening/restoring the stash tree; when
``stash apply --index`` is requested it may also flatten the stash's index
parent.  Foreign promised tree entries would otherwise materialize one blob at
a time while those snapshots are consumed.

This extension mirrors only the validation gates needed to predict demand.  The
existing Repository methods remain authoritative for stash traversal, worktree
mutation, index restoration, ref updates, errors, and return values.
"""

from __future__ import annotations

from functools import wraps
from typing import Type

from .objects import CommitObject
from .promisor import read_promisor_state
from .promisor_history import prefetch_history_promises


_INSTALLED = False


def _stash_worktree_dirty(state) -> bool:
    return any(
        state[key]
        for key in ("staged", "unstaged", "untracked", "conflicts")
    )


def _prefetch_stash_snapshots(repo, stash_sha: str, stash_obj: CommitObject, *, restore_index: bool) -> None:
    """Materialize stash snapshots that the historical restore will consume."""
    snapshots = [stash_sha]
    if restore_index and len(stash_obj.parents) >= 2:
        index_parent = stash_obj.parents[1]
        index_obj = repo.store.read(index_parent)
        if isinstance(index_obj, CommitObject):
            snapshots.append(index_parent)
    prefetch_history_promises(repo, snapshots)


def install_promisor_stash_support(repository_cls: Type) -> None:
    """Batch promised blobs before ``stash apply`` and ``stash pop`` mutate."""
    global _INSTALLED
    if _INSTALLED:
        return

    original_pop = repository_cls.stash_pop
    original_apply = repository_cls.stash_apply

    @wraps(original_pop)
    def stash_pop(self) -> str:
        state = read_promisor_state(self.pygit_dir)
        if not state.get("promised"):
            return original_pop(self)

        # Preserve the historical no-stash error without attempting a network
        # request.  Delegate the exact error text to the original method.
        stash_sha = self.refs.get_stash()
        if not stash_sha:
            return original_pop(self)

        # Do not prefetch a stash that cannot be applied because the user's
        # current worktree is dirty.  Phase228 may materialize HEAD while
        # computing status; that is independent status demand, not stash demand.
        current = self.status()
        if _stash_worktree_dirty(current):
            return original_pop(self)

        stash_obj = self._require_commit(stash_sha)
        _prefetch_stash_snapshots(
            self,
            stash_sha,
            stash_obj,
            restore_index=False,
        )
        return original_pop(self)

    @wraps(original_apply)
    def stash_apply(self, index: int = 0, restore_index: bool = False) -> str:
        state = read_promisor_state(self.pygit_dir)
        if not state.get("promised"):
            return original_apply(self, index=index, restore_index=restore_index)

        # Resolve the same stash entry before any fetch.  Out-of-range requests
        # remain local errors and do not pull unrelated promised objects.
        stashes = self.stash_list()
        if not stashes or index >= len(stashes):
            return original_apply(self, index=index, restore_index=restore_index)

        stash_sha, stash_obj = stashes[index]
        current = self.status()
        if _stash_worktree_dirty(current):
            return original_apply(self, index=index, restore_index=restore_index)

        _prefetch_stash_snapshots(
            self,
            stash_sha,
            stash_obj,
            restore_index=restore_index,
        )
        return original_apply(self, index=index, restore_index=restore_index)

    repository_cls.stash_pop = stash_pop
    repository_cls.stash_apply = stash_apply
    _INSTALLED = True
