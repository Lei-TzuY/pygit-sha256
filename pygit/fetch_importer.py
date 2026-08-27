"""Tag-preserving native importer for the modern configured fetch path.

The historical :class:`pygit.remote.NativeImporter` predates pygit's full
``TagObject`` support and peels native tag objects to their target SHA.  That
was sufficient when fetch treated tags as aliases, but it loses annotation
metadata and makes the fetch side asymmetric with Phase174's tag-aware native
exporter.

Phase183 keeps the legacy importer behavior untouched for compatibility and
uses this narrow subclass only in the modern configured fetch path.
"""

from __future__ import annotations

from typing import Tuple

from .objects import Identity, TagObject
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
