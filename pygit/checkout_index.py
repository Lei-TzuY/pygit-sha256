"""Index-to-worktree checkout plumbing backing ``checkout-index``.

This module materializes index entries without moving HEAD or changing refs.
Stage 0 remains the default for backward compatibility, while callers may
select conflict stages 1 (base), 2 (ours), or 3 (theirs) explicitly. It
intentionally follows the useful core of Git's checkout-index: selected paths
or ``--all``, force-overwrite behavior, temporary prefixes, and correct
handling for regular files and symbolic links.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import List, Sequence

from .index_plumbing import _matches_path, _path_exists
from .objects import BlobObject
from .repo import Repository


_VALID_STAGES = {0, 1, 2, 3}


def _safe_target(repo: Repository, relative: str, prefix: str = "") -> Path:
    root = Path(os.path.abspath(str(repo.worktree)))
    root_real = root.resolve()
    if prefix:
        prefix_path = Path(prefix)
        base = prefix_path if prefix_path.is_absolute() else root / prefix_path
    else:
        base = root

    target = Path(os.path.abspath(str(base / relative)))
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"checkout target is outside the repository: {target}") from exc

    # Resolve the parent only: an existing target may itself be a symlink that
    # --force is supposed to replace without following it.
    parent_real = target.parent.resolve(strict=False)
    try:
        parent_real.relative_to(root_real)
    except ValueError as exc:
        raise ValueError(f"checkout target escapes through a symlinked parent: {target}") from exc

    pygit = (root / ".pygit").resolve()
    try:
        parent_real.relative_to(pygit)
    except ValueError:
        pass
    else:
        raise ValueError("cannot write checkout output inside .pygit")
    return target


def _remove_existing(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    if path.exists():
        raise IsADirectoryError(f"cannot overwrite directory: {path}")


def _write_entry(
    repo: Repository,
    path: str,
    *,
    stage: int,
    force: bool,
    prefix: str,
) -> Path:
    entry = repo.index.get(path, stage)
    if entry is None:
        if stage == 0:
            raise KeyError(f"path is not in the index: {path}")
        raise KeyError(f"path has no stage {stage} index entry: {path}")
    if entry.mode == "160000":
        raise ValueError(f"cannot checkout submodule index entry: {path}")

    obj = repo.store.read(entry.sha)
    if not isinstance(obj, BlobObject):
        raise ValueError(f"index entry {path!r} does not reference a blob")

    target = _safe_target(repo, path, prefix)
    if _path_exists(target):
        if not force:
            raise FileExistsError(f"{target}: already exists (use --force)")
        _remove_existing(target)

    target.parent.mkdir(parents=True, exist_ok=True)
    if entry.mode == "120000":
        link_target = obj.data.decode("utf-8", "surrogateescape")
        target.symlink_to(link_target)
    else:
        target.write_bytes(obj.data)
        current = target.stat().st_mode
        if entry.mode == "100755":
            target.chmod(current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        else:
            target.chmod(current & ~(stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
    return target


def _paths_for_stage(repo: Repository, stage: int) -> List[str]:
    if stage == 0:
        return repo.index.paths()
    return sorted(
        {
            entry.path
            for entry in repo.index.stage_entries()
            if entry.stage == stage
        }
    )


def checkout_index(
    repo: Repository,
    paths: Sequence[str] = (),
    *,
    all_entries: bool = False,
    force: bool = False,
    prefix: str = "",
    stage: int = 0,
) -> List[Path]:
    """Materialize selected index entries and return the written paths.

    Explicit paths may be exact names, directory prefixes, or glob patterns.
    ``all_entries=True`` selects every entry at the requested stage. Stage 0 is
    the historical/default staging area; stages 1, 2, and 3 expose the merge
    base, ours, and theirs records of an unmerged path. Existing filesystem
    objects are protected unless ``force=True``.
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
    for path in sorted(set(selected)):
        written.append(
            _write_entry(
                repo,
                path,
                stage=stage,
                force=force,
                prefix=prefix,
            )
        )
    return written
