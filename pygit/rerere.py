"""
pygit/rerere.py
===============
Reuse Recorded Resolution (rerere) Engine

Records merge conflict preimages and resolutions in ``.pygit/rr-cache/<hash>/``,
allowing automated resolution when identical conflicts recur.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import List, Optional, Tuple


class RerereEngine:
    """Manages the .pygit/rr-cache directory for recording and reusing conflict resolutions."""

    def __init__(self, pygit_dir: Path) -> None:
        self.rr_cache_dir = pygit_dir / "rr-cache"
        self.enabled = True

    def _conflict_hash(self, conflict_text: str) -> str:
        """Compute SHA-256 digest for conflict text normalization."""
        norm = "\n".join(
            line.strip()
            for line in conflict_text.splitlines()
            if not line.startswith("<<<<<<<") and not line.startswith("=======") and not line.startswith(">>>>>>>")
        )
        return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:32]

    def record_conflict(self, path: str, conflict_text: str) -> str:
        """Record conflict preimage in rr-cache and return resolution hash."""
        h = self._conflict_hash(conflict_text)
        entry_dir = self.rr_cache_dir / h
        entry_dir.mkdir(parents=True, exist_ok=True)

        preimage = entry_dir / "preimage"
        if not preimage.exists():
            preimage.write_text(conflict_text, encoding="utf-8")
        return h

    def record_resolution(self, conflict_hash: str, resolved_text: str) -> bool:
        """Save resolved postimage for a previously recorded conflict hash."""
        entry_dir = self.rr_cache_dir / conflict_hash
        if not entry_dir.exists():
            return False
        postimage = entry_dir / "postimage"
        postimage.write_text(resolved_text, encoding="utf-8")
        return True

    def find_resolution(self, conflict_text: str) -> Optional[str]:
        """Check if a resolution exists for conflict_text and return resolved content."""
        h = self._conflict_hash(conflict_text)
        postimage = self.rr_cache_dir / h / "postimage"
        if postimage.exists():
            return postimage.read_text(encoding="utf-8")
        return None

    def status(self) -> List[Tuple[str, str]]:
        """Return list of (conflict_hash, path) tuples in rr-cache."""
        if not self.rr_cache_dir.exists():
            return []
        res = []
        for p in self.rr_cache_dir.iterdir():
            if p.is_dir():
                has_post = (p / "postimage").exists()
                st = "resolved" if has_post else "unresolved"
                res.append((p.name, st))
        return res
