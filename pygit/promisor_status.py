"""Promisor-aware batching for repository status reads.

``Repository.status`` compares the SHA-256-native index with a flattened HEAD
snapshot.  In a partial clone, flattening a foreign native-reference tree may
otherwise resolve promised blobs one entry at a time.  Phase228 predicts that
single HEAD snapshot demand and materializes it once before the historical
status implementation starts comparing paths.
"""

from __future__ import annotations

from functools import wraps
from typing import Type

from .promisor import read_promisor_state
from .promisor_history import prefetch_history_promises


_INSTALLED = False


def install_promisor_status_support(repository_cls: Type) -> None:
    """Batch unresolved HEAD promises before ``Repository.status``."""
    global _INSTALLED
    if _INSTALLED:
        return

    original_status = repository_cls.status

    @wraps(original_status)
    def status(self, ignored: bool = False):
        state = read_promisor_state(self.pygit_dir)
        if state.get("promised"):
            head = self.refs.resolve_head()
            if head:
                prefetch_history_promises(self, (head,))

        return original_status(self, ignored=ignored)

    repository_cls.status = status
    _INSTALLED = True
