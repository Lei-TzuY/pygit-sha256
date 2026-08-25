"""Side-effect-free three-way tree merge plumbing.

``merge_tree`` computes a merge result from two commit-ish revisions and their
best common ancestor without moving HEAD or touching the index/worktree. Clean
results are materialized as ordinary SHA-256 blob/tree objects so callers can
inspect or reuse the resulting tree.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Tuple

from .objects import BlobObject, CommitObject, TreeEntry, TreeObject
from .plumbing import merge_bases, resolve_commit
from .repo import Repository
from .tree_plumbing import flatten_tree


Entry = Tuple[str, str]  # (oid, mode)
Snapshot = Dict[str, Entry]
_MERGEABLE_BLOB_MODES = {"100644", "100755", "120000"}


@dataclass(frozen=True)
class MergeTreeResult:
    """Result of a side-effect-free tree merge."""

    tree_oid: Optional[str]
    base_oid: Optional[str]
    ours_oid: str
    theirs_oid: str
    conflicts: Tuple[str, ...]

    @property
    def clean(self) -> bool:
        return not self.conflicts


def _commit_snapshot(repo: Repository, oid: str) -> Snapshot:
    obj = repo.store.read(oid)
    if not isinstance(obj, CommitObject):
        raise RuntimeError(f"Object {oid} in merge ancestry is not a commit")
    return dict(flatten_tree(repo, obj.tree))


def _blob_bytes(repo: Repository, entry: Optional[Entry]) -> bytes:
    if entry is None:
        return b""
    oid, mode = entry
    if mode not in _MERGEABLE_BLOB_MODES:
        raise ValueError(f"mode {mode} is not a mergeable blob mode")
    obj = repo.store.read(oid)
    if not isinstance(obj, BlobObject):
        raise ValueError(f"object {oid} for mode {mode} is not a blob")
    return obj.data


def _merged_mode(base: Optional[Entry], ours: Entry, theirs: Entry) -> Optional[str]:
    if ours[1] == theirs[1]:
        return ours[1]
    if base is not None and ours[1] == base[1]:
        return theirs[1]
    if base is not None and theirs[1] == base[1]:
        return ours[1]
    return None


def _merge_entry(
    repo: Repository,
    path: str,
    base: Optional[Entry],
    ours: Optional[Entry],
    theirs: Optional[Entry],
) -> Tuple[Optional[Entry], bool]:
    # Identical results, including both sides deleting the path.
    if ours == theirs:
        return ours, False
    # One side unchanged from the base: take the other side verbatim.
    if ours == base:
        return theirs, False
    if theirs == base:
        return ours, False

    # Remaining delete/modify and add/add-different cases need content merging;
    # a deletion on either side cannot participate safely.
    if ours is None or theirs is None:
        return None, True
    if ours[1] not in _MERGEABLE_BLOB_MODES or theirs[1] not in _MERGEABLE_BLOB_MODES:
        return None, True
    if base is not None and base[1] not in _MERGEABLE_BLOB_MODES:
        return None, True

    mode = _merged_mode(base, ours, theirs)
    if mode is None:
        return None, True

    base_bytes = _blob_bytes(repo, base)
    ours_bytes = _blob_bytes(repo, ours)
    theirs_bytes = _blob_bytes(repo, theirs)

    # Avoid lossy text decoding for binary data. Exact/one-side-unchanged cases
    # were already handled above.
    if b"\x00" in base_bytes or b"\x00" in ours_bytes or b"\x00" in theirs_bytes:
        return None, True

    merged, has_conflict = Repository._merge_lines_three_way(
        base_bytes, ours_bytes, theirs_bytes, path
    )
    if has_conflict:
        return None, True
    oid = repo.store.write(BlobObject(merged))
    return (oid, mode), False


def _write_tree(repo: Repository, snapshot: Mapping[str, Entry]) -> str:
    """Materialize a flat path snapshot as nested tree objects."""

    def build(prefix: str) -> str:
        files: Dict[str, Entry] = {}
        dirs = set()
        prefix_with_slash = f"{prefix}/" if prefix else ""
        for path, entry in snapshot.items():
            if prefix and not path.startswith(prefix_with_slash):
                continue
            rest = path[len(prefix_with_slash) :]
            if "/" in rest:
                dirs.add(rest.split("/", 1)[0])
            elif rest:
                files[rest] = entry

        entries: List[TreeEntry] = [
            TreeEntry(mode=mode, name=name, sha=oid)
            for name, (oid, mode) in sorted(files.items())
        ]
        for name in sorted(dirs):
            child_prefix = f"{prefix}/{name}" if prefix else name
            entries.append(TreeEntry(mode="040000", name=name, sha=build(child_prefix)))
        return repo.store.write(TreeObject(entries))

    return build("")


def merge_tree(repo: Repository, ours: str, theirs: str) -> MergeTreeResult:
    """Merge two commit-ish revisions without changing repository state.

    The best common ancestor is selected using the same graph plumbing as
    ``merge-base``. Criss-cross histories with multiple equally-good bases are
    rejected rather than silently choosing an arbitrary virtual base.
    """
    ours_oid = resolve_commit(repo, ours)
    theirs_oid = resolve_commit(repo, theirs)
    bases = merge_bases(repo, ours_oid, theirs_oid)
    if len(bases) > 1:
        raise RuntimeError(
            "merge-tree does not yet support multiple merge bases; "
            f"found {len(bases)}"
        )
    base_oid = bases[0] if bases else None

    base_snapshot = _commit_snapshot(repo, base_oid) if base_oid else {}
    ours_snapshot = _commit_snapshot(repo, ours_oid)
    theirs_snapshot = _commit_snapshot(repo, theirs_oid)

    merged: Snapshot = {}
    conflicts: List[str] = []
    for path in sorted(set(base_snapshot) | set(ours_snapshot) | set(theirs_snapshot)):
        entry, conflict = _merge_entry(
            repo,
            path,
            base_snapshot.get(path),
            ours_snapshot.get(path),
            theirs_snapshot.get(path),
        )
        if conflict:
            conflicts.append(path)
        elif entry is not None:
            merged[path] = entry

    tree_oid = None if conflicts else _write_tree(repo, merged)
    return MergeTreeResult(
        tree_oid=tree_oid,
        base_oid=base_oid,
        ours_oid=ours_oid,
        theirs_oid=theirs_oid,
        conflicts=tuple(conflicts),
    )
