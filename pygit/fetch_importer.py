"""Native importers for the modern fetch path.

The historical :class:`pygit.remote.NativeImporter` predates pygit's full
``TagObject`` support and peels native tag objects to their target SHA. That was
sufficient when fetch treated tags as aliases, but it loses annotation metadata
and makes the fetch side asymmetric with Phase174's tag-aware native exporter.

Phase183 keeps the legacy importer behavior untouched for compatibility and
uses ``TagPreservingNativeImporter`` in the modern configured fetch path.
Phase204 adds ``StableShallowNativeImporter`` for genuinely truncated native
commit graphs: imported commits preserve their original SHA-1 parent identities
inside the local content-addressed commit payload, so an omitted parent can be
resolved later without rewriting the child SHA-256 object.
"""

from __future__ import annotations

from typing import List, Tuple

from .foreign_commits import update_foreign_commit_map
from .objects import CommitObject, Identity, TagObject
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
    """Import a native graph even when shallow commit parents are absent.

    Trees remain ordinary SHA-256 pygit trees. Commits record the complete
    native parent list in ``parent-sha1`` headers and expose only parents whose
    native objects are already known locally. ObjectStore re-resolves those
    edges from the persistent foreign-commit map on every read, so deepening a
    repository reconnects history without changing any existing child object id.
    """

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
