"""
pygit/objects/tag.py
====================
An **annotated tag** object.

Unlike a lightweight tag (which is just a ref file in ``.pygit/refs/tags/``),
an annotated tag is a full Git object stored in the object store. It contains:
  - ``object`` : target SHA (usually a commit)
  - ``type``   : target object type (usually ``b"commit"``)
  - ``tag``    : tag name (e.g. ``"v1.0"``)
  - ``tagger`` : :class:`~pygit.objects.commit.Identity` + timestamp
  - ``message``: tag annotation message

On-disk payload format (following outer header):

    object <sha>\\n
    type commit\\n
    tag <name>\\n
    tagger <name> <email> <timestamp> <tz>\\n
    \\n
    <message>
"""

from __future__ import annotations
from typing import Optional
from .base import GitObject
from .commit import Identity


class TagObject(GitObject):
    """
    An annotated tag object.

    Attributes
    ----------
    target_sha  : SHA of the object being tagged
    target_type : b"commit", b"tree", or b"blob"
    tag_name    : name of the tag (e.g. "v1.0")
    tagger      : :class:`Identity` of the person creating the tag
    message     : tag annotation message
    """

    type_name = b"tag"

    def __init__(
        self,
        target_sha: str = "",
        target_type: bytes = b"commit",
        tag_name: str = "",
        tagger: Optional[Identity] = None,
        message: str = "",
    ) -> None:
        self.target_sha  = target_sha
        self.target_type = target_type
        self.tag_name    = tag_name
        self.tagger      = tagger or Identity("Unknown", "unknown@example.com")
        self.message     = message

    # ------------------------------------------------------------------
    # GitObject protocol
    # ------------------------------------------------------------------

    def serialize(self) -> bytes:
        type_str = self.target_type.decode() if isinstance(self.target_type, bytes) else str(self.target_type)
        lines = [
            f"object {self.target_sha}",
            f"type {type_str}",
            f"tag {self.tag_name}",
            f"tagger {self.tagger.encode()}",
            "",
            self.message,
        ]
        return "\n".join(lines).encode("utf-8")

    def deserialize(self, data: bytes) -> None:
        text = data.decode("utf-8")
        header_block, _, body = text.partition("\n\n")
        self.message = body
        for line in header_block.splitlines():
            if line.startswith("object "):
                self.target_sha = line[7:]
            elif line.startswith("type "):
                self.target_type = line[5:].encode("utf-8")
            elif line.startswith("tag "):
                self.tag_name = line[4:]
            elif line.startswith("tagger "):
                self.tagger = Identity.decode(line[7:])
