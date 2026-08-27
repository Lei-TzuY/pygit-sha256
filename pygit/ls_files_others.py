"""Worktree selectors for ``pygit ls-files --others``.

Phase 156 keeps worktree discovery separate from the index-only plumbing in
``index_plumbing``.  The helper intentionally returns individual file/symlink
paths so callers can combine them with cached/index selectors without changing
the readable JSON index format.
"""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import List, Sequence

from .ignore import IgnoreMatcher
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


def other_files(
    repo: Repository,
    *,
    ignored: bool = False,
    exclude_standard: bool = False,
    patterns: Sequence[str] = (),
) -> List[str]:
    """Return untracked worktree paths for ``ls-files --others``.

    ``exclude_standard`` applies pygit's standard ignore stack
    (``.pygitignore``, ``.gitignore``, and ``.pygit/info/exclude``).  With
    ``ignored=True`` the selection is inverted and only ignored untracked paths
    are returned.  Repository metadata and every index stage are always
    excluded from the result.
    """
    if ignored and not exclude_standard:
        raise ValueError("--ignored requires --exclude-standard")

    root = Path(repo.worktree)
    tracked = set(repo.index.paths(include_unmerged=True))
    matcher = IgnoreMatcher(root) if exclude_standard else None
    result: List[str] = []

    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        relative_dir = current_path.relative_to(root)

        # Never expose or traverse repository metadata.  Symlinked directories
        # are file-like index candidates because os.walk will not descend them.
        symlink_dirs = [name for name in dirs if (current_path / name).is_symlink()]
        dirs[:] = [
            name
            for name in dirs
            if name != ".pygit" and not (current_path / name).is_symlink()
        ]

        for name in [*files, *symlink_dirs]:
            path = (relative_dir / name).as_posix()
            if path in tracked or not _matches_path(path, patterns):
                continue

            is_ignored = matcher.is_ignored(path, is_dir=False) if matcher else False
            if ignored:
                if is_ignored:
                    result.append(path)
            elif not is_ignored:
                result.append(path)

    return sorted(result)
