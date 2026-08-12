"""
pygit/sparse.py
===============
Sparse checkout pattern management.

Manages pattern rules stored in ``.pygit/info/sparse-checkout``.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import List, Set


class SparseCheckout:
    """Manages sparse checkout patterns and working tree filtering."""

    def __init__(self, pygit_dir: Path) -> None:
        self.sparse_file = pygit_dir / "info" / "sparse-checkout"
        self.patterns: List[str] = []
        self.enabled: bool = False
        self.load()

    def load(self) -> None:
        """Load sparse patterns from disk if present."""
        if self.sparse_file.exists():
            self.enabled = True
            lines = self.sparse_file.read_text(encoding="utf-8").splitlines()
            self.patterns = [
                line.strip()
                for line in lines
                if line.strip() and not line.strip().startswith("#")
            ]
        else:
            self.enabled = False
            self.patterns = []

    def save(self) -> None:
        """Save sparse patterns to disk."""
        self.sparse_file.parent.mkdir(parents=True, exist_ok=True)
        self.sparse_file.write_text("\n".join(self.patterns) + "\n", encoding="utf-8")
        self.enabled = True

    def disable(self) -> bool:
        """Disable sparse checkout by removing the pattern file."""
        if self.sparse_file.exists():
            self.sparse_file.unlink()
            self.enabled = False
            self.patterns = []
            return True
        return False

    def matches(self, path: str) -> bool:
        """Return True if *path* should be checked out based on sparse patterns."""
        if not self.enabled or not self.patterns:
            return True

        # Normalize path separators
        norm_path = path.replace("\\", "/")
        included = False

        for pattern in self.patterns:
            pat = pattern.replace("\\", "/")
            negate = pat.startswith("!")
            if negate:
                pat = pat[1:]

            # Directory pattern ends with /
            is_dir_pat = pat.endswith("/")
            clean_pat = pat.rstrip("/")

            match = False
            if is_dir_pat:
                if norm_path.startswith(clean_pat + "/") or norm_path == clean_pat:
                    match = True
            else:
                if fnmatch.fnmatch(norm_path, pat) or fnmatch.fnmatch(norm_path, clean_pat):
                    match = True
                elif norm_path.startswith(clean_pat + "/"):
                    match = True

            if match:
                included = not negate

        return included

    def filter_paths(self, paths: Set[str]) -> Set[str]:
        """Filter a set of paths according to active sparse patterns."""
        if not self.enabled:
            return paths
        return {p for p in paths if self.matches(p)}
