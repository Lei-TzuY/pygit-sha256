"""Promisor-aware batching for commit-to-worktree replacement.

Several high-level operations restore an entire commit through the private
``Repository._replace_worktree_from_commit`` primitive: fast-forward merge,
merge abort, rebase transitions, bisect checkout/reset, hard reset, and the
historical clone path.  In a partial clone, consuming unresolved native tree
entries one at a time would otherwise degrade into one promisor fetch per blob.

Phase218 wraps that single primitive.  If the target snapshot still contains
promised blobs, the complete deduplicated set is materialized before the
historical replacement starts mutating the index or working tree.  Ordinary
repositories and already-materialized snapshots remain network-free.
"""

from __future__ import annotations

from functools import wraps
from typing import Optional, Set, Type

from .promisor import read_promisor_state
from .promisor_checkout import collect_checkout_promises
from .promisor_commit import install_promisor_commit_support
from .promisor_materialize import materialize_promised_objects
from .promisor_three_way import install_promisor_three_way_support


_INSTALLED = False


def collect_worktree_promises(repo, commit_sha: str) -> Set[str]:
    """Return unresolved promised blobs needed by a full worktree snapshot."""
    return collect_checkout_promises(repo, commit_sha)


def install_promisor_worktree_support(repository_cls: Type) -> None:
    """Wrap full commit worktree replacement with one promisor prefetch."""
    global _INSTALLED
    if _INSTALLED:
        return

    original_replace = repository_cls._replace_worktree_from_commit

    @wraps(original_replace)
    def _replace_worktree_from_commit(
        self,
        sha: str,
        remove_paths: Optional[set] = None,
    ) -> None:
        state = read_promisor_state(self.pygit_dir)
        if state.get("promised"):
            promises = collect_worktree_promises(self, sha)
            if promises:
                # Fetch before the original implementation removes files,
                # rewrites the index, or writes any worktree content.  This
                # keeps materialization failure atomic with respect to the
                # historical worktree transition.
                materialize_promised_objects(
                    self.pygit_dir,
                    sorted(promises),
                )

        return original_replace(self, sha, remove_paths=remove_paths)

    repository_cls._replace_worktree_from_commit = _replace_worktree_from_commit
    # Later promisor-aware extensions share this established installer hook so
    # public package import order remains stable while adding narrowly scoped
    # batching around existing Repository primitives.
    install_promisor_commit_support(repository_cls)
    install_promisor_three_way_support(repository_cls)
    _INSTALLED = True
