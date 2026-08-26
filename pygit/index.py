"""
pygit/index.py
==============
The **Staging Area** (Index / Cache)
=====================================

In Git, the index is a binary file (``.git/index``) that acts as the
*proposed next commit*. It can also carry unmerged conflict entries at stages
1 (base), 2 (ours), and 3 (theirs).

Our index is stored as plain JSON in ``.pygit/index`` for readability. Stage-0
records keep the historical schema exactly; unmerged records add ``"stage"``::

    {
      "path": "src/main.py",
      "sha":  "<64-hex>",
      "mode": "100644",
      "size": 1234,
      "mtime": 1717000000.0,
      "stage": 2
    }

The public ``entries`` mapping remains stage-0-only for backward compatibility.
Stages 1-3 live in ``unmerged`` and are exposed through explicit query helpers.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def _mode_for(path: Path) -> str:
    """Return the Git mode string for a file."""
    mode = path.stat().st_mode
    if stat.S_ISLNK(mode):
        return "120000"
    if mode & stat.S_IXUSR:
        return "100755"
    return "100644"


class IndexEntry:
    """A single stage entry in the staging area."""

    __slots__ = ("path", "sha", "mode", "size", "mtime", "stage")

    def __init__(
        self,
        path: str,
        sha: str,
        mode: str = "100644",
        size: int = 0,
        mtime: float = 0.0,
        stage: int = 0,
    ) -> None:
        if stage not in {0, 1, 2, 3}:
            raise ValueError(f"index stage must be 0, 1, 2, or 3, got {stage}")
        self.path = path
        self.sha = sha
        self.mode = mode
        self.size = size
        self.mtime = mtime
        self.stage = stage

    def to_dict(self) -> dict:
        result = {
            "path": self.path,
            "sha": self.sha,
            "mode": self.mode,
            "size": self.size,
            "mtime": self.mtime,
        }
        # Preserve byte-for-byte-compatible logical schema for all historical
        # stage-0 repositories; only unmerged entries need the new field.
        if self.stage:
            result["stage"] = self.stage
        return result

    @classmethod
    def from_dict(cls, d: dict) -> "IndexEntry":
        return cls(
            path=d["path"],
            sha=d["sha"],
            mode=d.get("mode", "100644"),
            size=d.get("size", 0),
            mtime=d.get("mtime", 0.0),
            stage=d.get("stage", 0),
        )

    def __repr__(self) -> str:
        suffix = f" stage={self.stage}" if self.stage else ""
        return f"IndexEntry({self.mode} {self.sha[:12]} {self.path}{suffix})"


class Index:
    """Readable stage-aware index backed by ``.pygit/index`` JSON.

    ``entries`` intentionally remains ``path -> stage-0 entry`` so older
    porcelain keeps its existing contract. ``unmerged`` maps ``(path, stage)``
    for stages 1-3.
    """

    def __init__(self, index_path: Path) -> None:
        self._path = index_path
        self.entries: Dict[str, IndexEntry] = {}
        self.unmerged: Dict[Tuple[str, int], IndexEntry] = {}
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        self.entries = {}
        self.unmerged = {}
        if not self._path.exists():
            return

        raw = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise RuntimeError("malformed index: top-level JSON value must be a list")

        seen = set()
        for record in raw:
            if not isinstance(record, dict):
                raise RuntimeError("malformed index: every entry must be a JSON object")
            entry = IndexEntry.from_dict(record)
            key = (entry.path, entry.stage)
            if key in seen:
                raise RuntimeError(
                    f"malformed index: duplicate stage {entry.stage} for {entry.path!r}"
                )
            seen.add(key)
            if entry.stage == 0:
                self.entries[entry.path] = entry
            else:
                self.unmerged[key] = entry

    def save(self) -> None:
        records = list(self.entries.values()) + list(self.unmerged.values())
        data = [
            entry.to_dict()
            for entry in sorted(records, key=lambda entry: (entry.path, entry.stage))
        ]
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def clear_unmerged(self, path: Optional[str] = None) -> None:
        """Drop conflict stages globally or for one path, without saving."""
        if path is None:
            self.unmerged.clear()
            return
        for key in [key for key in self.unmerged if key[0] == path]:
            self.unmerged.pop(key, None)

    def set_entry(self, entry: IndexEntry, *, resolve_path: bool = False) -> None:
        """Insert one stage entry without saving.

        ``resolve_path=True`` models a normal ``git add``-style resolution: all
        stages for the path are removed before the new entry is installed.
        """
        if resolve_path:
            self.entries.pop(entry.path, None)
            self.clear_unmerged(entry.path)
        if entry.stage == 0:
            self.entries[entry.path] = entry
        else:
            self.unmerged[(entry.path, entry.stage)] = entry

    def add(self, path: str, sha: str, file_path: Path) -> None:
        """Stage a worktree file, resolving any unmerged stages for *path*."""
        st = file_path.stat()
        entry = IndexEntry(
            path=path,
            sha=sha,
            mode=_mode_for(file_path),
            size=st.st_size,
            mtime=st.st_mtime,
            stage=0,
        )
        self.set_entry(entry, resolve_path=True)
        self.save()

    def remove(self, path: str) -> None:
        """Remove every stage for *path*."""
        self.entries.pop(path, None)
        self.clear_unmerged(path)
        self.save()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get(self, path: str, stage: int = 0) -> Optional[IndexEntry]:
        if stage not in {0, 1, 2, 3}:
            raise ValueError(f"index stage must be 0, 1, 2, or 3, got {stage}")
        if stage == 0:
            return self.entries.get(path)
        return self.unmerged.get((path, stage))

    def stage_entries(self, path: Optional[str] = None) -> List[IndexEntry]:
        records = self.unmerged.values()
        if path is not None:
            records = [entry for entry in records if entry.path == path]
        return sorted(records, key=lambda entry: (entry.path, entry.stage))

    def all_entries(self, include_unmerged: bool = False) -> List[IndexEntry]:
        records: List[IndexEntry] = list(self.entries.values())
        if include_unmerged:
            records.extend(self.unmerged.values())
        return sorted(records, key=lambda entry: (entry.path, entry.stage))

    def paths(self, include_unmerged: bool = False) -> List[str]:
        paths = set(self.entries)
        if include_unmerged:
            paths.update(path for path, _stage in self.unmerged)
        return sorted(paths)

    def has_unmerged(self, path: Optional[str] = None) -> bool:
        if path is None:
            return bool(self.unmerged)
        return any(candidate == path for candidate, _stage in self.unmerged)

    def __contains__(self, path: str) -> bool:
        return path in self.entries or self.has_unmerged(path)

    def __len__(self) -> int:
        return len(self.entries) + len(self.unmerged)
