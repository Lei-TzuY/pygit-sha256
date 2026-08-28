"""Promisor-aware hard-reset batching.

A partial clone may keep target-tree blobs promised until they are needed.  A
hard reset restores the complete target worktree, so resolving each missing
``TreeEntry.sha`` independently would degrade into one fetch per file.  This
module mirrors the checkout batching path: collect all unresolved promised
blobs before the reset mutates HEAD/index/worktree and materialize them in one
request.
"""

from __future__ import annotations

from functools import wraps
from typing import Type

from .promisor import read_promisor_state
from .promisor_checkout import collect_checkout_promises
from .promisor_materialize import materialize_promised_objects


_INSTALLED = False


def install_promisor_reset_support(repository_cls: Type) -> None:
    """Install a transparent promisor-aware wrapper around ``reset``."""
    global _INSTALLED
    if _INSTALLED:
        return

    original_reset = repository_cls.reset

    @wraps(original_reset)
    def reset(self, target: str = "HEAD", mode: str = "mixed"):
        # Soft and mixed reset never restore blob contents to the worktree.
        # Preserve their exact historical behavior and avoid network I/O.
        if mode == "hard":
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
    _INSTALLED = True
