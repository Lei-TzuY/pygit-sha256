"""
pygit/objects/tree.py
=====================
A **tree** object is Git's representation of a directory snapshot.

Ordinary pygit trees store local SHA-256 child object ids.  Phase212 adds a
second canonical representation for filtered foreign trees: entries retain the
original native Git SHA-1 identities so a tree can be content-addressed even
when a blob was deliberately omitted by a promisor remote.  Runtime reads fill
resolved local SHA-256 ids from persistent promisor metadata; Phase213 may also
attach a lazy resolver so accessing an unresolved entry materializes the
promised object on demand without rewriting the parent tree.
"""

from __future__ import annotations

import binascii
from dataclasses import dataclass
from typing import Callable, List, Optional

from .base import GitObject


_NATIVE_TREE_MAGIC = b"pygit-native-tree-v1\x00"


@dataclass(init=False)
class TreeEntry:
    """A single directory entry inside a tree object."""

    mode: str
    name: str
    _sha: str
    native_oid: Optional[str]
    _resolver: Optional[Callable[[str], Optional[str]]]

    def __init__(
        self,
        mode: str,
        name: str,
        sha: str = "",
        native_oid: Optional[str] = None,
    ) -> None:
        self.mode = mode
        self.name = name
        self._sha = sha
        self.native_oid = native_oid
        self._resolver = None

    @property
    def sha(self) -> str:
        if self._sha:
            return self._sha
        if self.native_oid and self._resolver is not None:
            resolved = self._resolver(self.native_oid)
            if resolved:
                self._sha = resolved
                return resolved
        if self.native_oid:
            from ..promisor import PromisorMissingError

            kind = "tree" if self.is_dir else "blob"
            raise PromisorMissingError(self.native_oid, kind)
        return ""

    @sha.setter
    def sha(self, value: str) -> None:
        self._sha = value

    def set_resolver(self, resolver: Optional[Callable[[str], Optional[str]]]) -> None:
        """Attach an ephemeral native->local resolver.

        The resolver is runtime-only and is deliberately excluded from the
        canonical tree serialization, preserving the tree's SHA-256 identity.
        """
        self._resolver = resolver

    @property
    def is_resolved(self) -> bool:
        return bool(self._sha)

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
        identity = self._sha or self.native_oid or ""
        suffix = " native" if self.native_oid is not None else ""
        return f"TreeEntry({self.mode} {kind} {identity[:12]}{suffix}  {self.name})"


class TreeObject(GitObject):
    """A directory snapshot in local or native-reference canonical form."""

    type_name = b"tree"

    def __init__(
        self,
        entries: List[TreeEntry] | None = None,
        *,
        native_entries: bool = False,
    ) -> None:
        self.entries: List[TreeEntry] = entries or []
        self.native_entries = native_entries

    def serialize(self) -> bytes:
        if self.native_entries:
            buf = bytearray(_NATIVE_TREE_MAGIC)
            for entry in sorted(self.entries, key=lambda e: e.name):
                if not entry.native_oid:
                    raise ValueError("native tree entry is missing its native SHA-1 oid")
                if len(entry.native_oid) != 40:
                    raise ValueError("native tree entry oid must be a 40-hex SHA-1")
                try:
                    raw_oid = bytes.fromhex(entry.native_oid)
                except ValueError as exc:
                    raise ValueError("native tree entry oid must be a 40-hex SHA-1") from exc
                buf.extend(entry.mode.encode())
                buf.extend(b" ")
                buf.extend(entry.name.encode())
                buf.extend(b"\x00")
                buf.extend(raw_oid)
            return bytes(buf)

        buf = bytearray()
        for entry in sorted(self.entries, key=lambda e: e.name):
            raw_sha = binascii.unhexlify(entry.sha)
            if len(raw_sha) != 32:
                raise ValueError("tree entry must reference a SHA-256 object")
            buf.extend(entry.mode.encode())
            buf.extend(b" ")
            buf.extend(entry.name.encode())
            buf.extend(b"\x00")
            buf.extend(raw_sha)
        return bytes(buf)

    def deserialize(self, data: bytes) -> None:
        self.entries = []
        if data.startswith(_NATIVE_TREE_MAGIC):
            self.native_entries = True
            i = len(_NATIVE_TREE_MAGIC)
            digest_size = 20
            while i < len(data):
                space = data.index(b" ", i)
                null = data.index(b"\x00", space)
                mode = data[i:space].decode()
                name = data[space + 1:null].decode()
                raw_oid = data[null + 1:null + 1 + digest_size]
                if len(raw_oid) != digest_size:
                    raise ValueError("truncated native tree entry")
                self.entries.append(
                    TreeEntry(
                        mode=mode,
                        name=name,
                        native_oid=raw_oid.hex(),
                    )
                )
                i = null + 1 + digest_size
            return

        self.native_entries = False
        i = 0
        while i < len(data):
            space = data.index(b" ", i)
            null = data.index(b"\x00", space)
            mode = data[i:space].decode()
            name = data[space + 1:null].decode()
            raw_sha = data[null + 1:null + 33]
            if len(raw_sha) != 32:
                raise ValueError("truncated tree entry")
            sha = binascii.hexlify(raw_sha).decode()
            self.entries.append(TreeEntry(mode=mode, name=name, sha=sha))
            i = null + 33

    def add_entry(self, mode: str, name: str, sha: str) -> None:
        if self.native_entries:
            raise RuntimeError("cannot mutate a native-reference foreign tree")
        self.entries = [e for e in self.entries if e.name != name]
        self.entries.append(TreeEntry(mode=mode, name=name, sha=sha))

    def __len__(self) -> int:
        return len(self.entries)
