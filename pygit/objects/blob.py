"""
pygit/objects/blob.py
=====================
A **blob** is Git's word for a plain file's content.

Nothing about a blob knows the file *name* — the name lives in a
Tree entry.  A blob stores only the raw bytes.  This is the key
insight behind Git's content-addressing: rename a file and nothing
in the object store changes; move the same content into two places
and only one blob is stored.

On-disk format (before zlib compression):

    b"blob <N>\\x00<N bytes of content>"

where N is the byte length of the content.
"""

from __future__ import annotations
from .base import GitObject


class BlobObject(GitObject):
    """
    Represents the contents of a single file (or any binary blob).

    Attributes
    ----------
    data : bytes
        Raw file content.  Everything else (filename, permissions) lives
        in the Tree that references this blob.
    """

    type_name = b"blob"

    def __init__(self, data: bytes = b"") -> None:
        self.data = data

    # ------------------------------------------------------------------
    # GitObject protocol
    # ------------------------------------------------------------------

    def serialize(self) -> bytes:
        """Blob payload is just the raw bytes — nothing extra."""
        return self.data

    def deserialize(self, data: bytes) -> None:
        """Restore blob from raw payload bytes."""
        self.data = data

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    @classmethod
    def from_file(cls, path: str) -> "BlobObject":
        """Create a BlobObject by reading a file from the working tree."""
        with open(path, "rb") as fh:
            return cls(fh.read())

    def __len__(self) -> int:
        return len(self.data)
