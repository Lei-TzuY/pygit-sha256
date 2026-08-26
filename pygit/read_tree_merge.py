"""Three-way ``read-tree -m`` index merge support.

This module builds on the persistent multi-stage index introduced in Phase 124.
It implements Git's low-level trivial three-tree merge rules without touching
worktree files: cleanly resolvable paths become stage 0, while unresolved paths
are stored as stages 1/2/3.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .index import IndexEntry
from .objects import BlobObject
from .repo import Repository
from .tree_plumbing import flatten_tree, resolve_treeish


TreeValue = Tuple[str, str]


def _index_entry(
    repo: Repository,
    path: str,
    value: TreeValue,
    *,
    stage: int,
) -> IndexEntry:
    oid, mode = value
    target = repo.worktree / path
    if target.exists() or target.is_symlink():
        stat = target.lstat()
        size = stat.st_size
        mtime = stat.st_mtime
    elif mode == "160000":
        size = 0
        mtime = 0.0
    else:
        obj = repo.store.read(oid)
        size = len(obj.data) if isinstance(obj, BlobObject) else 0
        mtime = 0.0
    return IndexEntry(path, oid, mode, size, mtime, stage=stage)


def _reject_directory_file_conflicts(paths: Iterable[str]) -> None:
    """Reject path sets that need Git's directory/file conflict machinery."""
    ordered = sorted(set(paths))
    present = set(ordered)
    for path in ordered:
        parts = path.split("/")
        for index in range(1, len(parts)):
            parent = "/".join(parts[:index])
            if parent in present:
                raise RuntimeError(
                    "read-tree -m directory/file conflicts are not supported: "
                    f"{parent!r} conflicts with {path!r}"
                )


def _clean_resolution(
    base: Optional[TreeValue],
    ours: Optional[TreeValue],
    theirs: Optional[TreeValue],
    *,
    aggressive: bool,
) -> tuple[bool, Optional[TreeValue]]:
    """Return ``(resolved, value)`` using native three-tree trivial rules.

    In default mode Git deliberately leaves deletion-only trivialities
    unmerged. ``--aggressive`` additionally resolves those cases.
    """
    if ours == theirs:
        if ours is not None or aggressive:
            return True, ours
        return False, None

    if ours == base:
        if theirs is not None or aggressive:
            return True, theirs
        return False, None

    if theirs == base:
        if ours is not None or aggressive:
            return True, ours
        return False, None

    return False, None


def read_tree_three_way(
    repo: Repository,
    base_treeish: str,
    ours_treeish: str,
    theirs_treeish: str,
    *,
    aggressive: bool = False,
) -> List[IndexEntry]:
    """Replace the index with a three-tree trivial merge.

    The operation is index-only and atomic at the JSON publication boundary:
    all three tree-ish values are resolved and flattened before the existing
    index is replaced.
    """
    base = flatten_tree(repo, resolve_treeish(repo, base_treeish))
    ours = flatten_tree(repo, resolve_treeish(repo, ours_treeish))
    theirs = flatten_tree(repo, resolve_treeish(repo, theirs_treeish))

    paths = set(base) | set(ours) | set(theirs)
    _reject_directory_file_conflicts(paths)

    entries: Dict[str, IndexEntry] = {}
    unmerged: Dict[Tuple[str, int], IndexEntry] = {}

    for path in sorted(paths):
        base_value = base.get(path)
        ours_value = ours.get(path)
        theirs_value = theirs.get(path)
        resolved, value = _clean_resolution(
            base_value,
            ours_value,
            theirs_value,
            aggressive=aggressive,
        )

        if resolved:
            if value is not None:
                entries[path] = _index_entry(repo, path, value, stage=0)
            continue

        for stage, candidate in (
            (1, base_value),
            (2, ours_value),
            (3, theirs_value),
        ):
            if candidate is None:
                continue
            entry = _index_entry(repo, path, candidate, stage=stage)
            unmerged[(path, stage)] = entry

    repo.index.entries = entries
    repo.index.unmerged = unmerged
    repo.index.save()
    return repo.index.all_entries(include_unmerged=True)
