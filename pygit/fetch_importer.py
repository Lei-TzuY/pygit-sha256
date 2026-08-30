"""Native importers for the modern fetch path.

The historical :class:`pygit.remote.NativeImporter` predates pygit's full
``TagObject`` support and peels native tag objects to their target SHA. That was
sufficient when fetch treated tags as aliases, but it loses annotation metadata
and makes the fetch side asymmetric with Phase174's tag-aware native exporter.

Phase183 keeps the legacy importer behavior untouched for compatibility and
uses ``TagPreservingNativeImporter`` in the modern configured fetch path.
Phase204 adds ``StableShallowNativeImporter`` for genuinely truncated native
commit graphs. Phase212 adds ``PromisorFilteredNativeImporter`` for filtered
packs that intentionally omit blob objects while retaining stable foreign-tree
identity through native SHA-1 entry references.
"""

from __future__ import annotations

from typing import List, Optional, Set, Tuple

from .foreign_commits import update_foreign_commit_map
from .objects import CommitObject, Identity, TagObject, TreeObject
from .objects.tree import TreeEntry
from .promisor import update_promisor_state
from .remote import NativeImporter, NativeObject


class TagPreservingNativeImporter(NativeImporter):
    """Convert native annotated tags into real SHA-256 ``TagObject`` objects."""

    def _convert_one(self, obj: NativeObject) -> str:
        if obj.type_name != "tag":
            return super()._convert_one(obj)

        target_oid, target_type, tag_name, tagger, message = self._parse_tag(obj.data)
        target_sha = self.converted[target_oid]
        return self.store.write(
            TagObject(
                target_sha=target_sha,
                target_type=target_type.encode("utf-8"),
                tag_name=tag_name,
                tagger=tagger,
                message=message,
            )
        )

    @classmethod
    def _parse_tag(cls, data: bytes) -> Tuple[str, str, str, Identity, str]:
        header, separator, message = data.decode(
            "utf-8", errors="replace"
        ).partition("\n\n")
        if not separator:
            message = ""

        target_oid = ""
        target_type = ""
        tag_name = ""
        tagger = Identity("Unknown", "unknown@example.com")

        for line in header.splitlines():
            if line.startswith("object "):
                target_oid = line[7:]
            elif line.startswith("type "):
                target_type = line[5:]
            elif line.startswith("tag "):
                tag_name = line[4:]
            elif line.startswith("tagger "):
                tagger = cls._parse_identity(line[7:])

        if not target_oid:
            raise ValueError("Remote tag object has no target")
        if not target_type:
            raise ValueError("Remote tag object has no target type")
        if not tag_name:
            raise ValueError("Remote tag object has no tag name")

        return target_oid, target_type, tag_name, tagger, message


class StableShallowNativeImporter(TagPreservingNativeImporter):
    """Import a native graph even when shallow commit parents are absent."""

    def _dependencies(self, obj: NativeObject) -> List[str]:
        if obj.type_name != "commit":
            return super()._dependencies(obj)
        tree, parents, _, _, _ = self._parse_commit(obj.data)
        available_parents = [
            oid for oid in parents if oid in self.objects or oid in self.converted
        ]
        return [tree, *available_parents]

    def _convert_one(self, obj: NativeObject) -> str:
        if obj.type_name != "commit":
            return super()._convert_one(obj)

        tree, native_parents, author, committer, message = self._parse_commit(obj.data)
        resolved_parents = [
            self.converted[oid] for oid in native_parents if oid in self.converted
        ]
        update_foreign_commit_map(
            self.store.root.parent,
            {
                native_oid: self.converted[native_oid]
                for native_oid in native_parents
                if native_oid in self.converted
            },
        )
        local_sha = self.store.write(
            CommitObject(
                tree=self.converted[tree],
                parents=resolved_parents,
                native_parents=native_parents,
                author=author,
                committer=committer,
                message=message,
            )
        )
        update_foreign_commit_map(
            self.store.root.parent,
            {obj.oid: local_sha},
        )
        return local_sha


class PromisorFilteredNativeImporter(TagPreservingNativeImporter):
    """Import filtered native packs while allowing promised blobs to be absent.

    Native Git tree objects remain in the pack for ``blob:none`` and
    ``blob:limit`` filters, but some blob entries may be omitted.  Foreign trees
    therefore store every original native entry oid in a special local canonical
    representation. Resolved children are exposed as real SHA-256 ids at runtime;
    unresolved children raise :class:`PromisorMissingError` on access.
    """

    def __init__(
        self,
        store,
        objects,
        known=None,
        *,
        remote: str,
        filter_spec: str,
    ) -> None:
        super().__init__(store, objects, known=known)
        self.remote = remote
        self.filter_spec = filter_spec
        self._initial_known: Set[str] = set(self.converted)
        self._tree_references: Set[str] = set()
        self._promised: dict[str, str] = {}

    @property
    def promised_native_oids(self) -> Tuple[str, ...]:
        """Return unresolved native promises discovered while importing trees."""

        return tuple(sorted(self._promised))

    def _dependencies(self, obj: NativeObject) -> List[str]:
        if obj.type_name != "tree":
            return super()._dependencies(obj)

        dependencies: List[str] = []
        for mode, _name, oid in self._parse_tree(obj.data):
            self._tree_references.add(oid)
            if mode == "040000":
                if oid not in self.objects and oid not in self.converted:
                    raise KeyError(f"Filtered pack is missing required tree {oid}")
                dependencies.append(oid)
            elif oid in self.objects or oid in self.converted:
                dependencies.append(oid)
            else:
                self._promised[oid] = "blob"
        return dependencies

    def _convert_one(self, obj: NativeObject) -> str:
        if obj.type_name != "tree":
            return super()._convert_one(obj)

        entries = [
            TreeEntry(
                mode=mode,
                name=name,
                sha=self.converted.get(oid, ""),
                native_oid=oid,
            )
            for mode, name, oid in self._parse_tree(obj.data)
        ]
        return self.store.write(TreeObject(entries, native_entries=True))

    def import_oid(self, oid: str) -> str:
        result = super().import_oid(oid)
        resolved = {
            native_oid: self.converted[native_oid]
            for native_oid in self._tree_references
            if native_oid in self.converted
        }
        update_promisor_state(
            self.store.root.parent,
            remote=self.remote,
            filter_spec=self.filter_spec,
            promised=self._promised,
            resolved=resolved,
        )
        return result
