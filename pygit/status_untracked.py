"""Presentation helpers for Git-style status untracked/ignored modes.

The repository status API intentionally reports individual filesystem paths.
This module keeps that low-level contract unchanged while status presentation
can expose Git's ``no``, ``normal`` and ``all`` untracked modes plus Phase 155's
``traditional``, ``matching`` and ``no`` ignored modes.
"""

from __future__ import annotations

from typing import Iterable, List, Set, Union

from .ignore import IgnoreMatcher
from .repo import Repository


_VALID_UNTRACKED_MODES = {"no", "normal", "all"}
_VALID_IGNORED_MODES = {"no", "traditional", "matching"}


def _tracked_paths(repo: Repository) -> Set[str]:
    """Return every index pathname, including unresolved stage entries."""
    paths = {entry.path for entry in repo.index.all_entries()}
    paths.update(entry.path for entry in repo.index.stage_entries())
    return paths


def _collapse_paths(paths: Iterable[str], tracked: Set[str]) -> List[str]:
    """Collapse paths beneath the earliest directory containing no tracked path."""
    output: Set[str] = set()
    tracked_list = tuple(tracked)

    for path in sorted(set(paths)):
        parts = path.split("/")
        display = path
        for depth in range(1, len(parts)):
            prefix = "/".join(parts[:depth])
            marker = prefix + "/"
            if not any(item == prefix or item.startswith(marker) for item in tracked_list):
                display = marker
                break
        output.add(display)
    return sorted(output)


def _matching_ignored_paths(repo: Repository, paths: Iterable[str]) -> List[str]:
    """Render ignored paths using Git's ``--ignored=matching`` semantics."""
    raw = sorted(set(paths))
    matcher = IgnoreMatcher(repo.worktree)

    directories: Set[str] = set()
    for path in raw:
        parts = path.split("/")
        for depth in range(1, len(parts)):
            directories.add("/".join(parts[:depth]))

    explicit_dirs = sorted(
        (path for path in directories if matcher.is_explicitly_ignored(path, is_dir=True)),
        key=lambda item: (item.count("/"), item),
    )
    selected_dirs: List[str] = []
    for directory in explicit_dirs:
        if any(directory == parent or directory.startswith(parent + "/") for parent in selected_dirs):
            continue
        selected_dirs.append(directory)

    output: Set[str] = {directory + "/" for directory in selected_dirs}
    for path in raw:
        if any(path == directory or path.startswith(directory + "/") for directory in selected_dirs):
            continue
        if matcher.is_explicitly_ignored(path, is_dir=False):
            output.add(path)

    return sorted(output)


def apply_status_path_modes(
    repo: Repository,
    result: dict,
    *,
    untracked_mode: str = "normal",
    ignored: Union[bool, str] = False,
    ignored_mode: str = "traditional",
) -> dict:
    """Return a copy of normalized status with display-level path grouping.

    ``ignored`` accepts the historical boolean contract and, for the status CLI
    pipeline, a mode string. This keeps existing Python callers compatible while
    allowing the parser to thread ``--ignored=matching`` without widening every
    intermediate presentation signature.
    """
    if untracked_mode not in _VALID_UNTRACKED_MODES:
        raise ValueError(f"invalid untracked-files mode: {untracked_mode!r}")

    if isinstance(ignored, str):
        ignored_mode = ignored
        show_ignored = ignored_mode != "no"
    else:
        show_ignored = bool(ignored)
    if ignored_mode not in _VALID_IGNORED_MODES:
        raise ValueError(f"invalid ignored mode: {ignored_mode!r}")

    updated = dict(result)
    tracked = _tracked_paths(repo)
    raw_untracked = list(result.get("untracked", []))

    if untracked_mode == "no":
        updated["untracked"] = []
    elif untracked_mode == "all":
        updated["untracked"] = sorted(raw_untracked)
    else:
        updated["untracked"] = _collapse_paths(raw_untracked, tracked)

    if "ignored" in result:
        raw_ignored = list(result.get("ignored", []))
        if not show_ignored:
            updated["ignored"] = []
        elif ignored_mode == "matching":
            updated["ignored"] = _matching_ignored_paths(repo, raw_ignored)
        else:
            updated["ignored"] = (
                sorted(raw_ignored)
                if untracked_mode == "all"
                else _collapse_paths(raw_ignored, tracked)
            )

    return updated
