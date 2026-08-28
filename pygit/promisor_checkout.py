"""Promisor-aware worktree checkout batching.

Phase213 can lazily materialize one promised object when a native-reference tree
entry is consumed. Phase214 adds a multi-object materializer for partial clone's
initial checkout, and Phase215 allows a filtered clone to skip checkout entirely.

Phase216 closes the next gap: a later ``Repository.checkout()`` on such a
no-checkout partial clone should not fall back to one network request per file.
Before the established checkout implementation starts flattening/restoring the
target tree, this extension collects all still-promised blobs in that snapshot
and materializes them in one batch. The original checkout implementation remains
responsible for index rebuilding, sparse filtering, HEAD/reflog updates and
post-checkout hooks.
"""

from __future__ import annotations

from functools import wraps
from typing import Set, Type

from .objects import CommitObject, TreeObject
from .promisor import promised_kind, read_promisor_state
from .promisor_materialize import materialize_promised_objects


_INSTALLED = False


def collect_checkout_promises(repo, commit_sha: str) -> Set[str]:
    """Return unresolved promised native blobs reachable from ``commit_sha``.

    Filtered packs are required to retain the commit/tree graph, so traversing
    directories is network-free. Only blob entries intentionally omitted by the
    promisor filter are collected. Resolved entries are skipped.
    """
    commit = repo.store.read(commit_sha)
    if not isinstance(commit, CommitObject):
        return set()

    promised: Set[str] = set()
    pending = [commit.tree]
    seen: Set[str] = set()
    while pending:
        tree_sha = pending.pop()
        if tree_sha in seen:
            continue
        seen.add(tree_sha)
        tree = repo.store.read(tree_sha)
        if not isinstance(tree, TreeObject):
            raise RuntimeError("promisor checkout commit references a non-tree object")

        for entry in tree.entries:
            if entry.is_dir:
                # blob:none/blob:limit filtered packs must retain trees. Accessing
                # this SHA therefore resolves locally and must not fetch a blob.
                pending.append(entry.sha)
                continue
            if entry.is_resolved:
                continue
            if entry.native_oid and promised_kind(repo.pygit_dir, entry.native_oid):
                promised.add(entry.native_oid)
    return promised


def install_promisor_checkout_support(repository_cls: Type) -> None:
    """Install one transparent promisor-aware wrapper around ``checkout``."""
    global _INSTALLED
    if _INSTALLED:
        return

    original_checkout = repository_cls.checkout

    @wraps(original_checkout)
    def checkout(self, target: str, orphan: bool = False):
        # Orphan checkout does not restore a commit tree and therefore must not
        # force any promised objects to materialize.
        if not orphan:
            state = read_promisor_state(self.pygit_dir)
            if state.get("promised"):
                # Match the established checkout resolver. Unknown/non-commit
                # targets are left to the original method so its error contract
                # remains authoritative.
                sha = self.refs.resolve(target)
                if sha:
                    promises = collect_checkout_promises(self, sha)
                    if promises:
                        materialize_promised_objects(
                            self.pygit_dir,
                            sorted(promises),
                        )

        return original_checkout(self, target, orphan=orphan)

    repository_cls.checkout = checkout
    _INSTALLED = True
