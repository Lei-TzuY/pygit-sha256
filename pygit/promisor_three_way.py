"""Promisor-aware batching for three-way history operations.

Phase224 closes a remaining partial-clone demand-fetch waterfall in true
three-way merge and replay operations.  pygit's historical merge machinery
flattens complete commit trees into SHA-256-native ``path -> (blob, mode)``
mappings before it can compare entries.  A foreign tree entry whose blob is
still promised has only a native Git SHA-1 until that blob is materialized, so
walking base/ours/theirs one entry at a time can otherwise trigger one network
request per missing blob.

The helper below keeps the existing merge/cherry-pick/rebase semantics and
changes only the fetch shape: collect the union of unresolved promised blobs
reachable from the commit snapshots that the historical operation is about to
flatten, then materialize that deduplicated set through the established
multi-promisor layer before tree consumption begins.

Native SHA-1 remains confined to promisor/protocol metadata.  Materialized
objects enter the normal store under their real content-derived SHA-256 ids; no
surrogate ids or alternate tree serialization are introduced.
"""

from __future__ import annotations

from functools import wraps
from typing import Iterable, Optional, Set, Type

from .objects import CommitObject
from .promisor import read_promisor_state
from .promisor_checkout import collect_checkout_promises
from .promisor_materialize import materialize_promised_objects


_INSTALLED = False


def collect_commit_promises(repo, commit_shas: Iterable[Optional[str]]) -> Set[str]:
    """Return the deduplicated unresolved blobs reachable from commit snapshots."""
    state = read_promisor_state(repo.pygit_dir)
    if not state.get("promised"):
        return set()

    promises: Set[str] = set()
    seen = set()
    for sha in commit_shas:
        if not sha or sha in seen:
            continue
        seen.add(sha)
        promises.update(collect_checkout_promises(repo, sha))
    return promises


def prefetch_commit_promises(repo, commit_shas: Iterable[Optional[str]]) -> Set[str]:
    """Materialize one union of promises before complete tree flattening."""
    promises = collect_commit_promises(repo, commit_shas)
    if promises:
        materialize_promised_objects(repo.pygit_dir, sorted(promises))
    return promises


def install_promisor_three_way_support(repository_cls: Type) -> None:
    """Batch promise demand for merge plus the shared cherry-pick/rebase primitive."""
    global _INSTALLED
    if _INSTALLED:
        return

    original_merge = repository_cls.merge
    original_apply_cherry_pick = repository_cls._apply_cherry_pick

    @wraps(original_merge)
    def merge(
        self,
        target: str,
        message: Optional[str] = None,
        author_name: str = "Unknown",
        author_email: str = "unknown@example.com",
        squash: bool = False,
    ):
        state = read_promisor_state(self.pygit_dir)
        if state.get("promised"):
            # Preserve the historical error/atomicity boundary: a dirty worktree
            # or another in-progress operation must fail before any demand fetch.
            # The original method repeats these checks after prefetch; that is
            # intentional and keeps it authoritative for merge semantics.
            self._ensure_no_operation("merge")
            self._ensure_clean_worktree("merge")

            ours = self.refs.resolve_head()
            theirs = self.refs.resolve(target)
            if ours and theirs and ours != theirs:
                ours_obj = self.store.read(ours)
                theirs_obj = self.store.read(theirs)
                if isinstance(ours_obj, CommitObject) and isinstance(theirs_obj, CommitObject):
                    base = self._find_merge_base(ours, theirs)
                    # Up-to-date and ordinary fast-forward merges do not enter
                    # the three-way tree flattener.  Fast-forward worktree demand
                    # is already batched by Phase218.  Squash of a fast-forwardable
                    # target still uses the three-way machinery and therefore does
                    # require this prefetch.
                    needs_three_way = (
                        base != theirs
                        and not (base == ours and not squash)
                    )
                    if needs_three_way:
                        prefetch_commit_promises(
                            self,
                            (base, ours, theirs),
                        )

        return original_merge(
            self,
            target,
            message=message,
            author_name=author_name,
            author_email=author_email,
            squash=squash,
        )

    @wraps(original_apply_cherry_pick)
    def _apply_cherry_pick(self, source_sha: str, target: str):
        source = self.store.read(source_sha)
        head_sha = self.refs.resolve_head()
        if (
            isinstance(source, CommitObject)
            and len(source.parents) <= 1
            and head_sha
        ):
            # This private primitive is shared by top-level cherry-pick and every
            # rebase replay step.  Prefetch before its historical implementation
            # flattens source-parent/current-HEAD/source trees and before it writes
            # any merge result into the worktree or index.
            prefetch_commit_promises(
                self,
                (
                    source.parents[0] if source.parents else None,
                    head_sha,
                    source_sha,
                ),
            )

        return original_apply_cherry_pick(self, source_sha, target)

    repository_cls.merge = merge
    repository_cls._apply_cherry_pick = _apply_cherry_pick
    _INSTALLED = True
