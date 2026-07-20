"""
pygit/objects/tree.py
=====================
A **tree** object is Git's representation of a directory snapshot.

Each entry in a tree records:
  - a Unix-style mode (e.g. "100644" for a regular file, "040000" for a dir)
  - a name  (the filename or sub-directory name — NOT the full path)
  - the SHA-256 hash of a blob (for a file) or another tree (for a sub-dir)

This is how Git builds directory hierarchies out of a flat object store:
trees point to blobs (leaf files) and to other trees (sub-directories).
The root tree of a commit is the snapshot of the entire project at that
moment in time.

On-disk format (before zlib, after the outer header is stripped):

    For each entry, concatenated:
        <mode> SP <name> NUL <20-byte binary hash>
                              ^^^^^^^^^^^^^^^^^^^
                              raw bytes, NOT hex; for sha256 use 32 bytes

Since we use SHA-256 (32-byte digests) we store 32 raw bytes per entry.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List
import binascii
from .base import GitObject


@dataclass
class TreeEntry:
    """A single directory entry inside a tree object."""
    mode: str    # e.g. "100644", "100755", "040000", "120000"
    name: str    # filename or directory name (no path separators)
    sha: str     # hex digest of the referenced blob or tree

    # Convenience helpers for mode interpretation
    @property
    def is_dir(self) -> bool:
        return self.mode == "040000"

    @property
    def is_executable(self) -> bool:
        return self.mode == "100755"

    @property
    def is_symlink(self) -> bool:
        return self.mode == "120000"

    def __repr__(self) -> str:
        kind = "tree" if self.is_dir else "blob"
        return f"TreeEntry({self.mode} {kind} {self.sha[:12]}  {self.name})"


class TreeObject(GitObject):
    """
    A directory snapshot.

    ``entries`` is a list of :class:`TreeEntry` objects, sorted by name
    (Git sorts tree entries lexicographically — directories sort as if
    they had a trailing slash).
    """

    type_name = b"tree"

    def __init__(self, entries: List[TreeEntry] | None = None) -> None:
        self.entries: List[TreeEntry] = entries or []

    # ------------------------------------------------------------------
    # GitObject protocol
    # ------------------------------------------------------------------

    def serialize(self) -> bytes:
        """
        Encode all entries as::

            <mode>SP<name>NUL<32-byte-raw-sha>  (repeated)
        """
        buf = b""
        for entry in sorted(self.entries, key=lambda e: e.name):
            raw_sha = binascii.unhexlify(entry.sha)
            buf += (
                entry.mode.encode()
                + b" "
                + entry.name.encode()
                + b"\x00"
                + raw_sha
            )
        return buf

    def deserialize(self, data: bytes) -> None:
        """Parse concatenated tree-entry bytes back into ``self.entries``."""
        self.entries = []
        i = 0
        while i < len(data):
            # Read "<mode> <name>\x00"
            space = data.index(b" ", i)
            null  = data.index(b"\x00", space)
            mode  = data[i:space].decode()
            name  = data[space + 1:null].decode()
            # Read 32-byte raw hash (SHA-256)
            raw_sha = data[null + 1: null + 33]
            sha = binascii.hexlify(raw_sha).decode()
            self.entries.append(TreeEntry(mode=mode, name=name, sha=sha))
            i = null + 33

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def add_entry(self, mode: str, name: str, sha: str) -> None:
        """Add or update an entry (by name)."""
        self.entries = [e for e in self.entries if e.name != name]
        self.entries.append(TreeEntry(mode=mode, name=name, sha=sha))

    def __len__(self) -> int:
        return len(self.entries)
