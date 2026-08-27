"""Compatibility controls layered on top of checkout-index materialization."""

from __future__ import annotations

from pathlib import Path
from typing import List, Sequence, Tuple

from .checkout_index import _paths_for_stage, _safe_target, _write_entry
from .index_plumbing import _matches_path, _path_exists
from .repo import Repository


_VALID_STAGES = {0, 1, 2, 3}


def checkout_index_controlled(
    repo: Repository,
    paths: Sequence[str] = (),
    *,
    all_entries: bool = False,
    force: bool = False,
    prefix: str = "",
    stage: int = 0,
    no_create: bool = False,
    update_index: bool = False,
) -> List[Path]:
    """Checkout index entries with Git-style creation/stat controls.

    ``no_create`` skips selected entries whose final checkout target does not
    already exist. ``update_index`` refreshes size/mtime for entries written to
    their normal worktree path. Native Git does not refresh index stat data when
    ``--prefix`` redirects output elsewhere, so prefixed writes intentionally
    leave the index untouched.
    """
    if stage not in _VALID_STAGES:
        raise ValueError(f"index stage must be 0, 1, 2, or 3, got {stage}")
    if not all_entries and not paths:
        raise ValueError("checkout-index requires paths or --all")

    index_paths = _paths_for_stage(repo, stage)
    if all_entries:
        selected = list(index_paths)
    else:
        selected = [path for path in index_paths if _matches_path(path, paths)]
        for pattern in paths:
            if not any(_matches_path(path, [pattern]) for path in index_paths):
                if stage == 0:
                    raise KeyError(f"pathspec {pattern!r} did not match any index entry")
                raise KeyError(
                    f"pathspec {pattern!r} did not match any stage-{stage} index entry"
                )

    written: List[Path] = []
    stat_updates: List[Tuple[str, int, float]] = []
    for path in sorted(set(selected)):
        target = _safe_target(repo, path, prefix)
        if no_create and not _path_exists(target):
            continue

        written_path = _write_entry(
            repo,
            path,
            stage=stage,
            force=force,
            prefix=prefix,
        )
        written.append(written_path)

        if update_index and not prefix:
            st = written_path.lstat()
            stat_updates.append((path, st.st_size, st.st_mtime))

    if stat_updates:
        for path, size, mtime in stat_updates:
            entry = repo.index.get(path, stage)
            if entry is None:
                raise RuntimeError(f"index entry disappeared during checkout: {path}")
            entry.size = size
            entry.mtime = mtime
        repo.index.save()

    return written
