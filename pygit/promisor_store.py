"""ObjectStore extension for Phase212 native-reference promisor trees."""

from __future__ import annotations

from .objects import TreeObject
from .promisor import resolved_native_objects
from .store import ObjectStore


_INSTALLED = False


def install_promisor_store_support() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    original_read = ObjectStore.read

    def read(self: ObjectStore, sha: str):
        obj = original_read(self, sha)
        if isinstance(obj, TreeObject) and getattr(obj, "native_entries", False):
            resolved = resolved_native_objects(self.root.parent)
            for entry in obj.entries:
                if entry.native_oid and not entry.is_resolved:
                    local_oid = resolved.get(entry.native_oid)
                    if local_oid:
                        entry.sha = local_oid
        return obj

    ObjectStore.read = read
    _INSTALLED = True
