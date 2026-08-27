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


def _matches_exclude(path: str, patterns: Sequence[str]) -> bool:
    """Return whether *path* matches an explicit ``ls-files`` exclude pattern.

    Slashless patterns match any path component so patterns such as ``*.tmp``
    apply recursively.  Repository-relative patterns containing a slash match
    the whole path.  A trailing slash denotes a directory prefix and therefore
    excludes its descendants as well.
    """
    normalized_path = path.strip("/")
    parts = normalized_path.split("/") if normalized_path else []

    for raw_pattern in patterns:
        pattern = raw_pattern.replace("\\", "/").strip()
        if not pattern or pattern.startswith("#"):
            continue

        directory_pattern = pattern.endswith("/")
        pattern = pattern.strip("/")
        if not pattern:
            continue

        if directory_pattern:
            if normalized_path == pattern or normalized_path.startswith(pattern + "/"):
                return True
            continue

        if "/" not in pattern:
            if any(fnmatch.fnmatchcase(part, pattern) for part in parts):
                return True
            continue

        if any(ch in pattern for ch in "*?["):
            if fnmatch.fnmatchcase(normalized_path, pattern):
                return True
        elif normalized_path == pattern or normalized_path.startswith(pattern + "/"):
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


def _tree_has_explicit_exclude(
    root: Path,
    directory: Path,
    patterns: Sequence[str],
) -> bool:
    """Return whether any descendant is selected by an explicit exclude.

    ``--directory`` may collapse a wholly-untracked tree only when doing so does
    not hide a mixture created by ``-x``/``-X`` patterns.  This conservative
    scan keeps directory output faithful when only part of a tree is excluded.
    """
    if not patterns:
        return False

    for current, dirs, files in os.walk(directory, topdown=True, followlinks=False):
        current_path = Path(current)
        for name in [*dirs, *files]:
            candidate = (current_path / name).relative_to(root).as_posix()
            if _matches_exclude(candidate, patterns):
                return True
        dirs[:] = [name for name in dirs if not (current_path / name).is_symlink()]
    return False


def other_files(
    repo: Repository,
    *,
    ignored: bool = False,
    exclude_standard: bool = False,
    exclude_patterns: Sequence[str] = (),
    patterns: Sequence[str] = (),
    directory: bool = False,
    no_empty_directory: bool = False,
) -> List[str]:
    """Return untracked worktree paths for ``ls-files --others``.

    ``exclude_standard`` applies pygit's standard ignore stack
    (``.pygitignore``, ``.gitignore``, and ``.pygit/info/exclude``), while
    ``exclude_patterns`` supplies explicit Git-style ``-x``/``-X`` patterns.
    With ``ignored=True`` the selection is inverted and only ignored untracked
    paths are returned.  ``directory`` collapses a wholly-untracked directory
    tree to a trailing-slash record when the directory itself belongs to the
    requested ignore class and no explicit descendant pattern would make that
    collapse lossy.  ``no_empty_directory`` suppresses directory trees
    containing no files or symlinks.

    Repository metadata and every index stage are always excluded from the
    result.
    """
    if ignored and not (exclude_standard or exclude_patterns):
        raise ValueError("--ignored requires --exclude-standard, --exclude, or --exclude-from")
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
                standard_ignored = matcher.is_ignored(path, is_dir=True) if matcher else False
                explicit_ignored = _matches_exclude(path, exclude_patterns)
                is_ignored_dir = standard_ignored or explicit_ignored
                selected_class = is_ignored_dir if ignored else not is_ignored_dir
                nonempty_ok = not no_empty_directory or _contains_filelike(child)
                partial_explicit_selection = (
                    bool(exclude_patterns)
                    and not explicit_ignored
                    and _tree_has_explicit_exclude(root, child, exclude_patterns)
                )

                if (
                    wholly_untracked
                    and pattern_allows_collapse
                    and selected_class
                    and nonempty_ok
                    and not partial_explicit_selection
                ):
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

            standard_ignored = matcher.is_ignored(path, is_dir=False) if matcher else False
            explicit_ignored = _matches_exclude(path, exclude_patterns)
            is_ignored = standard_ignored or explicit_ignored
            if ignored:
                if is_ignored:
                    result.append(path)
            elif not is_ignored:
                result.append(path)

    return sorted(result)
