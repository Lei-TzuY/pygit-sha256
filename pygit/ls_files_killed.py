"""Filesystem obstruction selector for ``pygit ls-files --killed``.

A killed path is an untracked file or symlink that prevents an index path from
being checked out because one side of the conflict is a file while the other
side needs that pathname to be a directory. Git exposes these obstructions via
``ls-files -k``; keeping the scan separate from index serialization preserves
pygit's readable JSON index model.
"""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import List, Sequence

from .repo import Repository


def _matches_path(path: str, patterns: Sequence[str]) -> bool:
    if not patterns:
        return True
    for pattern in patterns:
        pattern = pattern.strip("/")
        if any(ch in pattern for ch in "*?["):
            if fnmatch.fnmatchcase(path, pattern):
                return True
        elif path == pattern or path.startswith(pattern + "/"):
            return True
    return False


def _is_file_directory_conflict(candidate: str, tracked_paths: Sequence[str]) -> bool:
    candidate_prefix = candidate + "/"
    for tracked in tracked_paths:
        if tracked.startswith(candidate_prefix) or candidate.startswith(tracked + "/"):
            return True
    return False


def killed_files(repo: Repository, *, patterns: Sequence[str] = ()) -> List[str]:
    """Return untracked worktree files/symlinks obstructing tracked paths.

    Two directory/file conflict shapes are reported:

    * an untracked file exists where an indexed path needs a parent directory;
    * an indexed file pathname is currently a directory containing untracked
      files or symlinks.

    Ignore rules intentionally do not suppress killed paths, matching Git's
    obstruction-oriented behavior. Repository metadata is never traversed.
    """
    root = Path(repo.worktree)
    tracked = sorted(set(repo.index.paths(include_unmerged=True)))
    tracked_set = set(tracked)
    result: List[str] = []

    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        relative_dir = current_path.relative_to(root)

        symlink_dirs = [name for name in dirs if (current_path / name).is_symlink()]
        dirs[:] = [
            name
            for name in dirs
            if name != ".pygit" and not (current_path / name).is_symlink()
        ]

        for name in [*files, *symlink_dirs]:
            path = (relative_dir / name).as_posix()
            if path in tracked_set or not _matches_path(path, patterns):
                continue
            if _is_file_directory_conflict(path, tracked):
                result.append(path)

    return sorted(result)
