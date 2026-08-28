"""Promisor-aware reset batching.

A partial clone may keep target-tree blobs promised until they are needed.
Hard reset restores the target worktree, and mixed reset rebuilds pygit's index.
Both operations therefore need local SHA-256 blob identities for target entries.
Resolving each missing ``TreeEntry.sha`` independently would degrade into one
fetch per file, so this wrapper collects all unresolved promised blobs first and
materializes them in one request before reset mutates repository state.
"""

from __future__ import annotations

from functools import wraps
from typing import Type

from .promisor import read_promisor_state
from .promisor_checkout import collect_checkout_promises
from .promisor_materialize import materialize_promised_objects
from .promisor_reset_paths import install_promisor_reset_paths_support


_INSTALLED = False


def install_promisor_reset_support(repository_cls: Type) -> None:
    """Install transparent promisor-aware wrappers around reset operations."""
    global _INSTALLED
    if _INSTALLED:
        return

    original_reset = repository_cls.reset

    @wraps(original_reset)
    def reset(self, target: str = "HEAD", mode: str = "mixed"):
        # Soft reset moves only refs, so it never needs promised blob contents.
        # Mixed reset rebuilds pygit's SHA-256 index; unlike native Git, pygit
        # cannot store the foreign SHA-1 tree-entry identity directly there.
        if mode in {"mixed", "hard"}:
            state = read_promisor_state(self.pygit_dir)
            if state.get("promised"):
                # Resolve and materialize before the original reset moves HEAD.
                # If revision resolution or the promisor fetch fails, reset has
                # not mutated refs, the index, or the working tree yet.
                sha = self._resolve_revision(target)
                promises = collect_checkout_promises(self, sha)
                if promises:
                    materialize_promised_objects(
                        self.pygit_dir,
                        sorted(promises),
                    )

        return original_reset(self, target=target, mode=mode)

    repository_cls.reset = reset
    install_promisor_reset_paths_support(repository_cls)
    _INSTALLED = True
