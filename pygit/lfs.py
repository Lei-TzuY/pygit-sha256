"""
pygit/lfs.py
============
Git LFS (Large File Storage) Engine
===================================

Implements Git LFS Pointer Specification v1.

Pointer text format:
--------------------
version https://git-lfs.github.com/spec/v1
oid sha256:4a3b8e7c...
size 10485760

Stored payloads reside under:
-----------------------------
.pygit/lfs/objects/xx/yy/xxxx...
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
from pathlib import Path
from typing import List, Optional, Tuple

LFS_HEADER = "version https://git-lfs.github.com/spec/v1\n"


class LFSEngine:
    """Manages Git LFS pointers, tracked patterns, and payload storage."""

    def __init__(self, pygit_dir: Path, worktree: Path) -> None:
        self.pygit_dir = pygit_dir
        self.worktree = worktree
        self.lfs_dir = pygit_dir / "lfs" / "objects"
        self.lfs_dir.mkdir(parents=True, exist_ok=True)
        self.attributes_file = worktree / ".pygitattributes"

    def is_pointer(self, text_or_bytes: bytes | str) -> bool:
        if isinstance(text_or_bytes, bytes):
            try:
                text = text_or_bytes.decode("utf-8", errors="ignore")
            except Exception:
                return False
        else:
            text = text_or_bytes
        return text.startswith("version https://git-lfs.github.com/spec/v1") and "oid sha256:" in text

    def parse_pointer(self, text: str) -> Optional[Tuple[str, int]]:
        if not self.is_pointer(text):
            return None
        oid = ""
        size = 0
        for line in text.splitlines():
            if line.startswith("oid sha256:"):
                oid = line[11:].strip()
            elif line.startswith("size "):
                try:
                    size = int(line[5:].strip())
                except ValueError:
                    pass
        if oid and size >= 0:
            return oid, size
        return None

    def create_pointer(self, payload: bytes) -> Tuple[str, str, int]:
        """Compute payload SHA-256, store binary, and return LFS pointer text."""
        oid = hashlib.sha256(payload).hexdigest()
        size = len(payload)

        # Store binary payload in .pygit/lfs/objects/xx/yy/oid
        obj_dir = self.lfs_dir / oid[:2] / oid[2:4]
        obj_dir.mkdir(parents=True, exist_ok=True)
        obj_file = obj_dir / oid
        if not obj_file.exists():
            obj_file.write_bytes(payload)

        pointer_text = f"{LFS_HEADER}oid sha256:{oid}\nsize {size}\n"
        return pointer_text, oid, size

    def read_payload(self, oid: str) -> Optional[bytes]:
        """Fetch payload bytes for *oid* from local LFS store."""
        obj_file = self.lfs_dir / oid[:2] / oid[2:4] / oid
        if obj_file.exists():
            return obj_file.read_bytes()
        return None

    def track_pattern(self, pattern: str) -> None:
        """Add a pattern to .pygitattributes."""
        lines = []
        if self.attributes_file.exists():
            lines = self.attributes_file.read_text(encoding="utf-8").splitlines()
        
        entry = f"{pattern} filter=lfs diff=lfs merge=lfs -text"
        if entry not in lines:
            lines.append(entry)
            self.attributes_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def list_tracked_patterns(self) -> List[str]:
        if not self.attributes_file.exists():
            return []
        patterns = []
        for line in self.attributes_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "filter=lfs" in line:
                pat = line.split()[0]
                patterns.append(pat)
        return patterns

    def should_use_lfs(self, rel_path: str) -> bool:
        for pat in self.list_tracked_patterns():
            if fnmatch.fnmatch(rel_path, pat) or fnmatch.fnmatch(Path(rel_path).name, pat):
                return True
        return False
