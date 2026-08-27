"""Presentation helpers for Git-style status untracked-file modes.

The repository status API intentionally reports individual filesystem paths.
This module keeps that low-level contract unchanged while status presentation
can expose Git's ``no``, ``normal`` and ``all`` modes.  In ``normal`` mode a
purely untracked directory is represented once as ``dir/``; directories that
contain tracked/index paths remain expanded far enough to avoid hiding tracked
state.
"""

from __future__ import annotations

from typing import Iterable, List, Set

from .repo import Repository


_VALID_MODES = {"no", "normal", "all"}


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


def apply_status_path_modes(
    repo: Repository,
    result: dict,
    *,
    untracked_mode: str = "normal",
    ignored: bool = False,
) -> dict:
    """Return a copy of normalized status with display-level path grouping.

    ``no`` suppresses untracked records, ``normal`` reports files plus collapsed
    untracked directories, and ``all`` keeps each individual untracked file.
    Ignored paths follow Git's traditional mode: they are collapsed unless
    ``--untracked-files=all`` requests individual directory contents.
    """
    if untracked_mode not in _VALID_MODES:
        raise ValueError(f"invalid untracked-files mode: {untracked_mode!r}")

    updated = dict(result)
    tracked = _tracked_paths(repo)
    raw_untracked = list(result.get("untracked", []))

    if untracked_mode == "no":
        updated["untracked"] = []
    elif untracked_mode == "all":
        updated["untracked"] = sorted(raw_untracked)
    else:
        updated["untracked"] = _collapse_paths(raw_untracked, tracked)

    if ignored and "ignored" in result:
        raw_ignored = list(result.get("ignored", []))
        updated["ignored"] = (
            sorted(raw_ignored)
            if untracked_mode == "all"
            else _collapse_paths(raw_ignored, tracked)
        )

    return updated
