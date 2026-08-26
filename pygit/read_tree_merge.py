"""Two- and three-tree ``read-tree -m`` index merge support.

The merge helpers are deliberately index-only. Two-tree mode models Git's
fast-forward carry-forward rules for staged changes; three-tree mode stores
unresolved paths in persistent stages 1/2/3.
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


def _entry_value(entry: Optional[IndexEntry]) -> Optional[TreeValue]:
    if entry is None:
        return None
    return entry.sha, entry.mode


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


def read_tree_two_way(
    repo: Repository,
    head_treeish: str,
    merge_treeish: str,
) -> List[IndexEntry]:
    """Fast-forward the index from *head_treeish* to *merge_treeish*.

    Stage-0 changes already present in the index are carried forward whenever
    doing so is unambiguous: an index entry matching the old tree adopts the new
    tree; unchanged old/new tree entries preserve the index; an index entry that
    already matches the new tree is preserved. Conflicting simultaneous index
    and tree changes fail before publication. The worktree is never modified.
    """
    if repo.index.has_unmerged():
        raise RuntimeError("read-tree -m cannot run with unmerged index entries")

    head = flatten_tree(repo, resolve_treeish(repo, head_treeish))
    merge = flatten_tree(repo, resolve_treeish(repo, merge_treeish))
    current = dict(repo.index.entries)
    initial_checkout = not current

    paths = set(head) | set(merge) | set(current)
    _reject_directory_file_conflicts(paths)

    entries: Dict[str, IndexEntry] = {}
    for path in sorted(paths):
        head_value = head.get(path)
        merge_value = merge.get(path)
        current_entry = current.get(path)
        index_value = _entry_value(current_entry)

        # Native read-tree has one special initial-checkout exception: when an
        # empty index sees the same path in H and M, populate it from M instead
        # of interpreting the missing index entry as a staged deletion.
        if (
            initial_checkout
            and index_value is None
            and head_value == merge_value
            and merge_value is not None
        ):
            entries[path] = _index_entry(repo, path, merge_value, stage=0)
            continue

        # No local staged change relative to H: advance to M, including adds
        # and deletions.
        if index_value == head_value:
            if merge_value is not None:
                if current_entry is not None and merge_value == index_value:
                    entries[path] = current_entry
                else:
                    entries[path] = _index_entry(repo, path, merge_value, stage=0)
            continue

        # H and M did not change this path, so keep the local staged state.
        if head_value == merge_value:
            if current_entry is not None:
                entries[path] = current_entry
            continue

        # The local staged state already equals the destination tree.
        if index_value == merge_value:
            if current_entry is not None:
                entries[path] = current_entry
            continue

        # A path added only in the index is unrelated to either tree and can be
        # carried across the fast-forward.
        if head_value is None and merge_value is None and current_entry is not None:
            entries[path] = current_entry
            continue

        raise RuntimeError(
            "read-tree -m would overwrite staged changes for "
            f"{path!r}"
        )

    repo.index.entries = entries
    repo.index.unmerged = {}
    repo.index.save()
    return repo.index.all_entries()


def _clean_resolution(
    base: Optional[TreeValue],
    ours: Optional[TreeValue],
    theirs: Optional[TreeValue],
    *,
    aggressive: bool,
) -> tuple[bool, Optional[TreeValue]]:
    """Return ``(resolved, value)`` using native three-tree trivial rules."""
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
    """Replace the index with a three-tree trivial merge."""
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
