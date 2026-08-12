"""
pygit/eol.py
============
End-of-Line (EOL) Line Ending Normalizer Filter
===============================================

Handles conversion between CRLF (\r\n) and LF (\n) when explicitly configured in ``.pygitattributes``.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path


class EOLNormalizer:
    """Normalizes line endings for repository storage and worktree checkout."""

    def __init__(self, pygit_dir: Path, worktree: Path) -> None:
        self.attributes_file = worktree / ".pygitattributes"

    def should_normalize(self, rel_path: str) -> bool:
        if not self.attributes_file.exists():
            return False
        for line in self.attributes_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and ("text" in line or "eol=" in line):
                pat = line.split()[0]
                if fnmatch.fnmatch(rel_path, pat) or fnmatch.fnmatch(Path(rel_path).name, pat):
                    return True
        return False

    @staticmethod
    def normalize_to_repo(data: bytes) -> bytes:
        """Convert all CRLF line endings to LF before storing in ObjectStore."""
        return data.replace(b"\r\n", b"\n")

    @staticmethod
    def normalize_to_worktree(data: bytes, target_eol: str = "lf") -> bytes:
        """Convert LF to target EOL (crlf or lf) during checkout."""
        normalized = data.replace(b"\r\n", b"\n")
        if target_eol.lower() == "crlf":
            return normalized.replace(b"\n", b"\r\n")
        return normalized
