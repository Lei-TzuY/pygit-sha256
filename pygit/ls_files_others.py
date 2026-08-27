"""Worktree selectors for ``pygit ls-files --others``.

Worktree discovery stays separate from the index-only plumbing in
``index_plumbing``.  The helper can return individual file/symlink paths or,
with ``directory=True``, collapse wholly-untracked directory trees to Git-style
``dir/`` records.
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


def _has_tracked_descendant(path: str, tracked: set[str]) -> bool:
    prefix = path + "/"
    return path in tracked or any(candidate.startswith(prefix) for candidate in tracked)


def _contains_filelike(path: Path) -> bool:
    """Return whether a directory tree contains a file or symlink.

    Git's ``--no-empty-directory`` suppresses trees made only from empty
    directories.  Symlinks count as file-like entries because Git does not
    traverse them as directories.
    """
    for current, dirs, files in os.walk(path, topdown=True, followlinks=False):
        current_path = Path(current)
        if files:
            return True
        symlink_dirs = [name for name in dirs if (current_path / name).is_symlink()]
        if symlink_dirs:
            return True
        dirs[:] = [name for name in dirs if not (current_path / name).is_symlink()]
    return False


def other_files(
    repo: Repository,
    *,
    ignored: bool = False,
    exclude_standard: bool = False,
    patterns: Sequence[str] = (),
    directory: bool = False,
    no_empty_directory: bool = False,
) -> List[str]:
    """Return untracked worktree paths for ``ls-files --others``.

    ``exclude_standard`` applies pygit's standard ignore stack
    (``.pygitignore``, ``.gitignore``, and ``.pygit/info/exclude``).  With
    ``ignored=True`` the selection is inverted and only ignored untracked paths
    are returned.  ``directory`` collapses a wholly-untracked directory tree to
    a trailing-slash record when the directory itself belongs to the requested
    ignore class.  ``no_empty_directory`` suppresses directory trees containing
    no files or symlinks.

    Repository metadata and every index stage are always excluded from the
    result.
    """
    if ignored and not exclude_standard:
        raise ValueError("--ignored requires --exclude-standard")
    if no_empty_directory and not directory:
        raise ValueError("--no-empty-directory requires --directory")

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
        ordinary_dirs = [
            name
            for name in dirs
            if name != ".pygit" and not (current_path / name).is_symlink()
        ]

        if directory:
            descend: List[str] = []
            for name in ordinary_dirs:
                child = current_path / name
                path = (relative_dir / name).as_posix()
                wholly_untracked = not _has_tracked_descendant(path, tracked)
                pattern_allows_collapse = _matches_path(path, patterns)
                is_ignored_dir = matcher.is_ignored(path, is_dir=True) if matcher else False
                selected_class = is_ignored_dir if ignored else not is_ignored_dir
                nonempty_ok = not no_empty_directory or _contains_filelike(child)

                if wholly_untracked and pattern_allows_collapse and selected_class and nonempty_ok:
                    result.append(path + "/")
                else:
                    descend.append(name)
            dirs[:] = descend
        else:
            dirs[:] = ordinary_dirs

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
