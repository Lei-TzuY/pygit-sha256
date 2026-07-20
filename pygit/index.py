"""
pygit/index.py
==============
The **Staging Area** (Index / Cache)
=====================================

In Git, the index is a binary file (``.git/index``) that acts as the
*proposed next commit*.  It maps every tracked path to a blob SHA plus
some metadata (file size, mtime, permissions …).

When you run ``git add``, you're updating entries in the index.
When you run ``git commit``, Git builds a tree from the index and wraps
it in a commit object.

Our index is stored as plain JSON in ``.pygit/index`` for readability.

Schema (per entry)::

    {
      "path": "src/main.py",
      "sha":  "<64-hex>",
      "mode": "100644",
      "size": 1234,
      "mtime": 1717000000.0
    }
"""

from __future__ import annotations
import json
import os
import stat
from pathlib import Path
from typing import Dict, List, Optional


def _mode_for(path: Path) -> str:
    """Return the Git mode string for a file."""
    mode = path.stat().st_mode
    if stat.S_ISLNK(mode):
        return "120000"
    if mode & stat.S_IXUSR:
        return "100755"
    return "100644"


class IndexEntry:
    """A single entry in the staging area."""

    __slots__ = ("path", "sha", "mode", "size", "mtime")

    def __init__(
        self,
        path:  str,
        sha:   str,
        mode:  str   = "100644",
        size:  int   = 0,
        mtime: float = 0.0,
    ) -> None:
        self.path  = path
        self.sha   = sha
        self.mode  = mode
        self.size  = size
        self.mtime = mtime

    def to_dict(self) -> dict:
        return {
            "path":  self.path,
            "sha":   self.sha,
            "mode":  self.mode,
            "size":  self.size,
            "mtime": self.mtime,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "IndexEntry":
        return cls(
            path  = d["path"],
            sha   = d["sha"],
            mode  = d.get("mode",  "100644"),
            size  = d.get("size",  0),
            mtime = d.get("mtime", 0.0),
        )

    def __repr__(self) -> str:
        return f"IndexEntry({self.mode} {self.sha[:12]} {self.path})"


class Index:
    """
    The staging area.

    Backed by ``.pygit/index`` (JSON format for readability).

    Attributes
    ----------
    entries : dict[str, IndexEntry]
        Path → entry map.
    """

    def __init__(self, index_path: Path) -> None:
        self._path = index_path
        self.entries: Dict[str, IndexEntry] = {}
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if self._path.exists():
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            self.entries = {
                d["path"]: IndexEntry.from_dict(d) for d in raw
            }

    def save(self) -> None:
        data = [e.to_dict() for e in sorted(
            self.entries.values(), key=lambda e: e.path
        )]
        self._path.write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add(self, path: str, sha: str, file_path: Path) -> None:
        """Stage a file (add / update)."""
        st = file_path.stat()
        self.entries[path] = IndexEntry(
            path  = path,
            sha   = sha,
            mode  = _mode_for(file_path),
            size  = st.st_size,
            mtime = st.st_mtime,
        )
        self.save()

    def remove(self, path: str) -> None:
        """Unstage / remove a path."""
        self.entries.pop(path, None)
        self.save()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get(self, path: str) -> Optional[IndexEntry]:
        return self.entries.get(path)

    def all_entries(self) -> List[IndexEntry]:
        return sorted(self.entries.values(), key=lambda e: e.path)

    def paths(self) -> List[str]:
        return sorted(self.entries.keys())

    def __contains__(self, path: str) -> bool:
        return path in self.entries

    def __len__(self) -> int:
        return len(self.entries)
