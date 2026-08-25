"""Index-to-worktree checkout plumbing backing ``checkout-index``.

This module materializes stage-0 index entries without moving HEAD or changing
refs. It intentionally follows the useful core of Git's checkout-index:
selected paths or ``--all``, force-overwrite behavior, temporary prefixes, and
correct handling for regular files and symbolic links.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import List, Sequence

from .index_plumbing import _matches_path, _path_exists
from .objects import BlobObject
from .repo import Repository


def _safe_target(repo: Repository, relative: str, prefix: str = "") -> Path:
    root = Path(os.path.abspath(str(repo.worktree)))
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
    pygit = (root / ".pygit").resolve()
    try:
        target.resolve(strict=False).relative_to(pygit)
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


def _write_entry(repo: Repository, path: str, *, force: bool, prefix: str) -> Path:
    entry = repo.index.get(path)
    if entry is None:
        raise KeyError(f"path is not in the index: {path}")
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


def checkout_index(
    repo: Repository,
    paths: Sequence[str] = (),
    *,
    all_entries: bool = False,
    force: bool = False,
    prefix: str = "",
) -> List[Path]:
    """Materialize selected index entries and return the written paths.

    Explicit paths may be exact names, directory prefixes, or glob patterns.
    ``all_entries=True`` selects every stage-0 index entry. Existing filesystem
    objects are protected unless ``force=True``.
    """
    if not all_entries and not paths:
        raise ValueError("checkout-index requires paths or --all")

    index_paths = repo.index.paths()
    if all_entries:
        selected = list(index_paths)
    else:
        selected = [path for path in index_paths if _matches_path(path, paths)]
        for pattern in paths:
            if not any(_matches_path(path, [pattern]) for path in index_paths):
                raise KeyError(f"pathspec {pattern!r} did not match any index entry")

    written: List[Path] = []
    for path in sorted(set(selected)):
        written.append(_write_entry(repo, path, force=force, prefix=prefix))
    return written
