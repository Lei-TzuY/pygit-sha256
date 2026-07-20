"""
pygit/objects/base.py
=====================
Abstract base class for all Git object types.

Git is fundamentally a **content-addressed database**: every piece of data
is stored under a key that is the cryptographic hash (SHA-1 in real Git,
SHA-256 here) of its content.  Every object — whether it represents a
file (blob), a directory (tree), or a snapshot (commit) — follows the
same envelope format:

    <type> SP <size> NUL <raw-content>

This module defines that common interface.
"""

from __future__ import annotations
import hashlib
from abc import ABC, abstractmethod


HASH_ALGO = "sha256"   # real Git uses sha1; we use sha256 for security


class GitObject(ABC):
    """
    Base class for every object that lives in the object store.

    Subclasses must implement:
      - ``type_name`` – b"blob" / b"tree" / b"commit" / b"tag"
      - ``serialize()``   – return the raw byte payload (no header)
      - ``deserialize()`` – reconstruct object state from raw bytes
    """

    type_name: bytes  # declared by each concrete subclass

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    @abstractmethod
    def serialize(self) -> bytes:
        """Return the raw content bytes (without the header envelope)."""

    @abstractmethod
    def deserialize(self, data: bytes) -> None:
        """Populate object state from raw content bytes."""

    # ------------------------------------------------------------------
    # Header / envelope
    # ------------------------------------------------------------------

    def _build_store_bytes(self) -> bytes:
        """
        Wrap raw content in the Git object envelope::

            b"blob 42\\x00<42 bytes of content>"

        This is what gets hashed and written to disk.
        """
        raw = self.serialize()
        header = self.type_name + b" " + str(len(raw)).encode() + b"\x00"
        return header + raw

    # ------------------------------------------------------------------
    # Hashing
    # ------------------------------------------------------------------

    def hash(self) -> str:
        """Return the hex-digest SHA-256 hash of this object's store bytes."""
        store_bytes = self._build_store_bytes()
        return hashlib.new(HASH_ALGO, store_bytes).hexdigest()

    # ------------------------------------------------------------------
    # Human-readable repr
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} {self.hash()[:12]}>"
