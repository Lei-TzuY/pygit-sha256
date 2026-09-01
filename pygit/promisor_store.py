"""ObjectStore extensions for durable writes and native-reference promisor trees.

Resolved promisor entries are filled from persistent metadata immediately.
Unresolved entries receive an ephemeral resolver which materializes the promised
object only if a consumer later accesses ``TreeEntry.sha``.

The package-level ObjectStore extension hook also installs Phase370's durable
loose-object writer before layering the promisor-aware reader. Keeping both
ObjectStore extensions behind the existing installer preserves the public class
API while making the write durability boundary active for normal imports.
"""

from __future__ import annotations

from .durable_object_store import install_durable_object_store_support
from .objects import TreeObject
from .promisor import promised_kind, resolved_native_objects
from .store import ObjectStore


_INSTALLED = False


def install_promisor_store_support() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    install_durable_object_store_support()
    original_read = ObjectStore.read

    def read(self: ObjectStore, sha: str):
        obj = original_read(self, sha)
        if isinstance(obj, TreeObject) and getattr(obj, "native_entries", False):
            pygit_dir = self.root.parent
            resolved = resolved_native_objects(pygit_dir)

            def resolve(native_oid: str):
                current = resolved_native_objects(pygit_dir).get(native_oid)
                if current:
                    return current
                if promised_kind(pygit_dir, native_oid) is None:
                    return None
                from .promisor_materialize import materialize_promised_object

                return materialize_promised_object(pygit_dir, native_oid)

            for entry in obj.entries:
                if not entry.native_oid or entry.is_resolved:
                    continue
                local_oid = resolved.get(entry.native_oid)
                if local_oid:
                    entry.sha = local_oid
                else:
                    entry.set_resolver(resolve)
        return obj

    ObjectStore.read = read
    _INSTALLED = True

    # Integrity checks must be installed after the lazy native-tree reader so
    # they can distinguish a resolved local SHA-256 from an intentionally
    # absent native promisor identity without triggering that resolver.
    from .promisor_fsck import install_promisor_fsck_support

    install_promisor_fsck_support()
