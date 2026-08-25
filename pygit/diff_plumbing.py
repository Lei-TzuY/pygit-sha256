"""Low-level diff plumbing across trees, the index, and the working tree.

This module implements a focused SHA-256 analogue of ``diff-tree``,
``diff-index`` and ``diff-files``.  The API is intentionally record-oriented:
callers get deterministic path-sorted metadata and may render it as Git-style
raw, name-status, or name-only output.
"""

from __future__ import annotations

import fnmatch
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .objects import BlobObject, CommitObject, TagObject, TreeObject
from .repo import Repository
from .revision import resolve_revision
from .tree_plumbing import flatten_tree


ZERO_OID = "0" * 64
_INDEX_MODES = {"100644", "100755", "120000", "160000"}


@dataclass(frozen=True)
class DiffEntry:
    path: str
    old_mode: str
    new_mode: str
    old_oid: str
    new_oid: str
    status: str


Snapshot = Dict[str, Tuple[str, str]]  # path -> (oid, mode)


def _validate_repo_path(path: str) -> Tuple[str, ...]:
    if not path or path.startswith(("/", "\\")) or "\x00" in path:
        raise ValueError(f"invalid repository path: {path!r}")
    if "\\" in path:
        raise ValueError(f"repository paths must use '/' separators: {path!r}")
    parts = tuple(path.split("/"))
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"invalid repository path: {path!r}")
    return parts


def _safe_worktree_path(repo: Repository, path: str) -> Path:
    parts = _validate_repo_path(path)
    root = repo.worktree.resolve()
    candidate = root.joinpath(*parts)
    parent = candidate.parent.resolve()
    try:
        parent.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"working-tree path escapes repository: {path!r}") from exc
    return candidate


def _mode_kind(mode: str) -> str:
    if mode in {"100644", "100755"}:
        return "file"
    if mode == "120000":
        return "symlink"
    if mode == "160000":
        return "gitlink"
    if mode == "040000":
        return "directory"
    if mode == "000000":
        return "missing"
    return mode


def _status(old: Optional[Tuple[str, str]], new: Optional[Tuple[str, str]]) -> str:
    if old is None:
        return "A"
    if new is None:
        return "D"
    if _mode_kind(old[1]) != _mode_kind(new[1]):
        return "T"
    return "M"


def _matches(path: str, patterns: Sequence[str]) -> bool:
    if not patterns:
        return True
    for pattern in patterns:
        if not pattern:
            continue
        value = pattern.rstrip("/")
        if any(char in value for char in "*?["):
            if fnmatch.fnmatchcase(path, value):
                return True
        elif path == value or path.startswith(value + "/"):
            return True
    return False


def _diff_snapshots(
    old: Mapping[str, Tuple[str, str]],
    new: Mapping[str, Tuple[str, str]],
    *,
    patterns: Sequence[str] = (),
) -> List[DiffEntry]:
    result: List[DiffEntry] = []
    for path in sorted(set(old) | set(new)):
        if not _matches(path, patterns):
            continue
        before = old.get(path)
        after = new.get(path)
        if before == after:
            continue
        result.append(
            DiffEntry(
                path=path,
                old_mode=before[1] if before else "000000",
                new_mode=after[1] if after else "000000",
                old_oid=before[0] if before else ZERO_OID,
                new_oid=after[0] if after else ZERO_OID,
                status=_status(before, after),
            )
        )
    return result


def tree_snapshot(repo: Repository, treeish: str) -> Snapshot:
    """Resolve a tree-ish through the shared revision layer and flatten it."""
    oid = resolve_revision(repo, treeish)
    seen = set()
    while True:
        if oid in seen:
            raise RuntimeError(f"tag cycle while resolving {treeish!r}")
        seen.add(oid)
        obj = repo.store.read(oid)
        if isinstance(obj, TreeObject):
            return dict(flatten_tree(repo, oid))
        if isinstance(obj, CommitObject):
            return dict(flatten_tree(repo, obj.tree))
        if isinstance(obj, TagObject):
            oid = obj.target_sha
            continue
        raise ValueError(f"tree-ish {treeish!r} resolves to a non-tree object")


def index_snapshot(repo: Repository) -> Snapshot:
    result: Snapshot = {}
    for entry in repo.index.all_entries():
        _validate_repo_path(entry.path)
        if entry.mode not in _INDEX_MODES:
            raise ValueError(f"unsupported index mode {entry.mode!r} at {entry.path!r}")
        if not repo.store.exists(entry.sha):
            raise KeyError(f"Object not found: {entry.sha}")
        obj = repo.store.read(entry.sha)
        if entry.mode == "160000":
            if not isinstance(obj, CommitObject):
                raise ValueError(f"gitlink {entry.path!r} does not reference a commit")
        elif not isinstance(obj, BlobObject):
            raise ValueError(f"index path {entry.path!r} does not reference a blob")
        result[entry.path] = (entry.sha, entry.mode)
    return result


def _hash_blob(data: bytes) -> str:
    return BlobObject(data).hash()


def worktree_snapshot(
    repo: Repository,
    paths: Iterable[str],
    *,
    gitlink_fallback: Optional[Mapping[str, Tuple[str, str]]] = None,
) -> Snapshot:
    """Snapshot selected tracked paths without adding untracked files.

    For gitlinks, pygit does not yet model nested repository HEAD state.  An
    existing directory therefore keeps the fallback gitlink OID/mode; a missing
    gitlink is still reported as deleted.
    """
    result: Snapshot = {}
    fallback = gitlink_fallback or {}
    for path in sorted(set(paths)):
        target = _safe_worktree_path(repo, path)
        exists = target.exists() or target.is_symlink()
        if not exists:
            continue

        expected = fallback.get(path)
        if expected and expected[1] == "160000" and target.is_dir():
            result[path] = expected
            continue

        st = target.lstat()
        if stat.S_ISLNK(st.st_mode):
            data = os.readlink(target).encode("utf-8", "surrogateescape")
            result[path] = (_hash_blob(data), "120000")
            continue
        if stat.S_ISREG(st.st_mode):
            data = target.read_bytes()
            mode = "100755" if st.st_mode & stat.S_IXUSR else "100644"
            result[path] = (_hash_blob(data), mode)
            continue
        if stat.S_ISDIR(st.st_mode):
            result[path] = (ZERO_OID, "040000")
            continue
        result[path] = (ZERO_OID, "000000")
    return result


def diff_tree(
    repo: Repository,
    left: str,
    right: Optional[str] = None,
    *,
    root: bool = False,
    patterns: Sequence[str] = (),
) -> List[DiffEntry]:
    """Compare two tree-ish values, or one commit against its first parent."""
    if right is not None:
        return _diff_snapshots(
            tree_snapshot(repo, left),
            tree_snapshot(repo, right),
            patterns=patterns,
        )

    oid = resolve_revision(repo, left)
    obj = repo.store.read(oid)
    if not isinstance(obj, CommitObject):
        raise ValueError("single-argument diff-tree requires a commit")
    new = dict(flatten_tree(repo, obj.tree))
    if obj.parents:
        parent = repo.store.read(obj.parents[0])
        if not isinstance(parent, CommitObject):
            raise ValueError(f"commit {oid} has a non-commit parent")
        old = dict(flatten_tree(repo, parent.tree))
    elif root:
        old = {}
    else:
        return []
    return _diff_snapshots(old, new, patterns=patterns)


def diff_index(
    repo: Repository,
    treeish: str,
    *,
    cached: bool = False,
    patterns: Sequence[str] = (),
) -> List[DiffEntry]:
    """Compare a tree-ish to the index or to the tracked working tree.

    ``cached=True`` compares tree -> index.  The default compares tree ->
    current worktree state for paths known by either the tree or index; untracked
    paths are intentionally omitted.
    """
    old = tree_snapshot(repo, treeish)
    index = index_snapshot(repo)
    if cached:
        new = index
    else:
        tracked = set(old) | set(index)
        fallback = dict(old)
        fallback.update(index)
        new = worktree_snapshot(repo, tracked, gitlink_fallback=fallback)
    return _diff_snapshots(old, new, patterns=patterns)


def diff_files(
    repo: Repository,
    *,
    patterns: Sequence[str] = (),
) -> List[DiffEntry]:
    """Compare index entries to their current working-tree state."""
    old = index_snapshot(repo)
    new = worktree_snapshot(repo, old, gitlink_fallback=old)
    return _diff_snapshots(old, new, patterns=patterns)


def format_diff_entries(
    entries: Sequence[DiffEntry],
    *,
    name_only: bool = False,
    name_status: bool = False,
    nul_terminated: bool = False,
) -> bytes:
    if name_only and name_status:
        raise ValueError("--name-only and --name-status are mutually exclusive")
    separator = b"\x00" if nul_terminated else b"\n"
    chunks: List[bytes] = []
    for entry in entries:
        if name_only:
            text = entry.path
        elif name_status:
            text = f"{entry.status}\t{entry.path}"
        else:
            text = (
                f":{entry.old_mode} {entry.new_mode} "
                f"{entry.old_oid} {entry.new_oid} {entry.status}\t{entry.path}"
            )
        chunks.append(text.encode("utf-8", "surrogateescape"))
    if not chunks:
        return b""
    return separator.join(chunks) + separator
