"""Promisor-aware batching for path-limited commits.

``Repository.commit(..., only_paths=...)`` historically rebuilds a temporary
index from the complete HEAD tree before applying the selected working-tree
paths.  In a partial clone that temporary SHA-256-native index needs real local
object ids for every HEAD blob.  Reading unresolved foreign tree entries one by
one would therefore degrade into one promisor fetch per blob.

Phase223 keeps the existing commit/index semantics and changes only the fetch
shape: when a path-limited commit starts from a partial-clone HEAD, collect the
complete deduplicated set of still-promised HEAD blobs and materialize them in
one request before the historical implementation traverses the tree.  Ordinary
commits and already-resolved snapshots remain network-free.

This intentionally does not invent surrogate SHA-256 ids.  pygit's persistent
index and ordinary tree objects require content-derived local SHA-256 object ids,
so all blobs copied from HEAD into the temporary index must be materialized.  A
future mixed native/local tree synthesis layer could narrow that object set; the
safe improvement here is to collapse the current N-request waterfall into one
bulk prefetch while preserving repository identity and commit behavior.
"""

from __future__ import annotations

from functools import wraps
from typing import List, Optional, Type

from .promisor import read_promisor_state
from .promisor_checkout import collect_checkout_promises
from .promisor_materialize import materialize_promised_objects


_INSTALLED = False


def install_promisor_commit_support(repository_cls: Type) -> None:
    """Batch unresolved HEAD blobs before ``commit(only_paths=...)``."""
    global _INSTALLED
    if _INSTALLED:
        return

    original_commit = repository_cls.commit

    @wraps(original_commit)
    def commit(
        self,
        message: str = "",
        author_name: str = "Unknown",
        author_email: str = "unknown@example.com",
        parents: Optional[List[str]] = None,
        committer_name: Optional[str] = None,
        committer_email: Optional[str] = None,
        allow_rebase: bool = False,
        amend: bool = False,
        template: Optional[str] = None,
        author: Optional[str] = None,
        fixup: Optional[str] = None,
        squash: Optional[str] = None,
        only_paths: Optional[List[str]] = None,
        include_paths: Optional[List[str]] = None,
        allow_empty: bool = False,
        cleanup: str = "strip",
        reuse_message: Optional[str] = None,
        reedit_message: Optional[str] = None,
        commit_date: Optional[str] = None,
        reset_author: bool = False,
        signoff: bool = False,
    ) -> str:
        if only_paths:
            state = read_promisor_state(self.pygit_dir)
            if state.get("promised"):
                head_sha = self.refs.resolve_head()
                if head_sha:
                    promises = collect_checkout_promises(self, head_sha)
                    if promises:
                        # The historical only-path commit immediately flattens
                        # HEAD into a temporary SHA-256-native index.  Prefetch
                        # before that traversal so it cannot trigger a separate
                        # lazy fetch for every foreign blob entry.
                        materialize_promised_objects(
                            self.pygit_dir,
                            sorted(promises),
                        )

        return original_commit(
            self,
            message=message,
            author_name=author_name,
            author_email=author_email,
            parents=parents,
            committer_name=committer_name,
            committer_email=committer_email,
            allow_rebase=allow_rebase,
            amend=amend,
            template=template,
            author=author,
            fixup=fixup,
            squash=squash,
            only_paths=only_paths,
            include_paths=include_paths,
            allow_empty=allow_empty,
            cleanup=cleanup,
            reuse_message=reuse_message,
            reedit_message=reedit_message,
            commit_date=commit_date,
            reset_author=reset_author,
            signoff=signoff,
        )

    repository_cls.commit = commit
    _INSTALLED = True
