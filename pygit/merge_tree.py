"""Three-way tree merge plumbing without ref/index/worktree mutation.

``merge_tree`` computes a merge from two commit-ish revisions and a merge base.
Clean results are materialized as ordinary SHA-256 blob/tree objects. Conflicted
results are reported structurally and do not write a result tree or pending
auto-merged blobs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Set, Tuple

from .diff_plumbing import Snapshot, tree_snapshot
from .objects import BlobObject, CommitObject, TagObject, TreeEntry, TreeObject
from .plumbing import merge_bases
from .repo import Repository
from .revision import resolve_revision


Entry = Tuple[str, str]
_REGULAR_MODES = {"100644", "100755"}


@dataclass(frozen=True)
class MergeConflict:
    """One path that cannot be merged automatically."""

    path: str
    reason: str
    base_oid: Optional[str] = None
    ours_oid: Optional[str] = None
    theirs_oid: Optional[str] = None
    base_mode: Optional[str] = None
    ours_mode: Optional[str] = None
    theirs_mode: Optional[str] = None


@dataclass(frozen=True)
class MergeTreeResult:
    """Structured result of a tree-only three-way merge."""

    tree_oid: Optional[str]
    base_oid: Optional[str]
    ours_oid: str
    theirs_oid: str
    conflicts: Tuple[MergeConflict, ...]
    changed_paths: Tuple[str, ...]

    @property
    def clean(self) -> bool:
        return self.tree_oid is not None and not self.conflicts


@dataclass(frozen=True)
class _MergedEntry:
    mode: str
    oid: Optional[str] = None
    data: Optional[bytes] = None

    def object_id(self) -> str:
        if self.oid is not None:
            return self.oid
        assert self.data is not None
        return BlobObject(self.data).hash()


def _commit_oid(repo: Repository, expression: str) -> str:
    """Resolve through the shared revision layer and peel tags to a commit."""
    oid = resolve_revision(repo, expression)
    seen: Set[str] = set()
    while True:
        if oid in seen:
            raise RuntimeError(f"tag cycle while resolving {expression!r}")
        seen.add(oid)
        obj = repo.store.read(oid)
        if isinstance(obj, CommitObject):
            return oid
        if isinstance(obj, TagObject):
            oid = obj.target_sha.lower()
            continue
        raise ValueError(f"{expression!r} does not resolve to a commit")


def _conflict(
    path: str,
    reason: str,
    base: Optional[Entry],
    ours: Optional[Entry],
    theirs: Optional[Entry],
) -> MergeConflict:
    return MergeConflict(
        path=path,
        reason=reason,
        base_oid=base[0] if base else None,
        ours_oid=ours[0] if ours else None,
        theirs_oid=theirs[0] if theirs else None,
        base_mode=base[1] if base else None,
        ours_mode=ours[1] if ours else None,
        theirs_mode=theirs[1] if theirs else None,
    )


def _existing(entry: Optional[Entry]) -> Optional[_MergedEntry]:
    if entry is None:
        return None
    return _MergedEntry(mode=entry[1], oid=entry[0])


def _kind(mode: str) -> str:
    if mode in _REGULAR_MODES:
        return "file"
    if mode == "120000":
        return "symlink"
    if mode == "160000":
        return "gitlink"
    return mode


def _blob_bytes(repo: Repository, entry: Entry, path: str) -> bytes:
    obj = repo.store.read(entry[0])
    if not isinstance(obj, BlobObject):
        raise ValueError(f"tree entry {path!r} does not reference a blob")
    return obj.data


def _merged_mode(base: str, ours: str, theirs: str) -> Optional[str]:
    if ours == theirs:
        return ours
    if ours == base:
        return theirs
    if theirs == base:
        return ours
    return None


def _merge_entry(
    repo: Repository,
    path: str,
    base: Optional[Entry],
    ours: Optional[Entry],
    theirs: Optional[Entry],
    their_label: str,
) -> Tuple[Optional[_MergedEntry], Optional[MergeConflict]]:
    if ours == theirs:
        return _existing(ours), None
    if ours == base:
        return _existing(theirs), None
    if theirs == base:
        return _existing(ours), None
    if base is None:
        return None, _conflict(path, "add/add", base, ours, theirs)
    if ours is None or theirs is None:
        return None, _conflict(path, "modify/delete", base, ours, theirs)

    base_kind = _kind(base[1])
    our_kind = _kind(ours[1])
    their_kind = _kind(theirs[1])
    if base_kind != our_kind or base_kind != their_kind:
        return None, _conflict(path, "type", base, ours, theirs)
    if our_kind == "symlink":
        return None, _conflict(path, "symlink", base, ours, theirs)
    if our_kind == "gitlink":
        return None, _conflict(path, "gitlink", base, ours, theirs)
    if our_kind != "file":
        return None, _conflict(path, "type", base, ours, theirs)

    mode = _merged_mode(base[1], ours[1], theirs[1])
    if mode is None:
        return None, _conflict(path, "mode", base, ours, theirs)
    if ours[0] == theirs[0]:
        return _MergedEntry(mode=mode, oid=ours[0]), None
    if ours[0] == base[0]:
        return _MergedEntry(mode=mode, oid=theirs[0]), None
    if theirs[0] == base[0]:
        return _MergedEntry(mode=mode, oid=ours[0]), None

    base_data = _blob_bytes(repo, base, path)
    our_data = _blob_bytes(repo, ours, path)
    their_data = _blob_bytes(repo, theirs, path)
    if b"\x00" in base_data or b"\x00" in our_data or b"\x00" in their_data:
        return None, _conflict(path, "binary", base, ours, theirs)

    merged, has_conflict = Repository._merge_lines_three_way(
        base_data, our_data, their_data, their_label,
    )
    if has_conflict:
        return None, _conflict(path, "content", base, ours, theirs)
    return _MergedEntry(mode=mode, data=merged), None


def _directory_file_conflicts(snapshot: Mapping[str, _MergedEntry]) -> Set[str]:
    paths = set(snapshot)
    result: Set[str] = set()
    for path in paths:
        parts = path.split("/")
        for index in range(1, len(parts)):
            parent = "/".join(parts[:index])
            if parent in paths:
                result.add(parent)
    return result


def _materialize_snapshot(repo: Repository, pending: Mapping[str, _MergedEntry]) -> Snapshot:
    snapshot: Snapshot = {}
    for path, entry in pending.items():
        if entry.oid is not None:
            oid = entry.oid
        else:
            assert entry.data is not None
            oid = repo.store.write(BlobObject(entry.data))
        snapshot[path] = (oid, entry.mode)
    return snapshot


def _write_snapshot_tree(repo: Repository, snapshot: Mapping[str, Entry]) -> str:
    root: Dict[str, object] = {}
    for path, entry in sorted(snapshot.items()):
        parts = path.split("/")
        node = root
        for part in parts[:-1]:
            child = node.setdefault(part, {})
            if not isinstance(child, dict):
                raise RuntimeError(f"tree path conflict at {path!r}")
            node = child
        if parts[-1] in node:
            raise RuntimeError(f"duplicate tree path: {path!r}")
        node[parts[-1]] = entry

    def write_node(node: Mapping[str, object]) -> str:
        entries: List[TreeEntry] = []
        for name in sorted(node):
            value = node[name]
            if isinstance(value, dict):
                entries.append(TreeEntry("040000", name, write_node(value)))
            else:
                oid, mode = value  # type: ignore[misc]
                entries.append(TreeEntry(mode, name, oid))
        return repo.store.write(TreeObject(entries))

    return write_node(root)


def merge_tree(
    repo: Repository,
    ours: str,
    theirs: str,
    *,
    base: Optional[str] = None,
    allow_unrelated_histories: bool = False,
) -> MergeTreeResult:
    """Merge two commit-ish values without changing refs, index, or worktree."""
    ours_oid = _commit_oid(repo, ours)
    theirs_oid = _commit_oid(repo, theirs)

    if base is not None:
        base_oid: Optional[str] = _commit_oid(repo, base)
    else:
        bases = merge_bases(repo, ours_oid, theirs_oid)
        if len(bases) > 1:
            raise RuntimeError("multiple merge bases found; pass an explicit merge base")
        if not bases:
            if not allow_unrelated_histories:
                raise RuntimeError(
                    "refusing to merge unrelated histories without --allow-unrelated-histories"
                )
            base_oid = None
        else:
            base_oid = bases[0]

    base_snapshot: Snapshot = tree_snapshot(repo, base_oid) if base_oid else {}
    our_snapshot: Snapshot = tree_snapshot(repo, ours_oid)
    their_snapshot: Snapshot = tree_snapshot(repo, theirs_oid)

    pending: Dict[str, _MergedEntry] = {}
    conflicts: List[MergeConflict] = []
    changed: Set[str] = set()
    all_paths = sorted(set(base_snapshot) | set(our_snapshot) | set(their_snapshot))

    for path in all_paths:
        base_entry = base_snapshot.get(path)
        our_entry = our_snapshot.get(path)
        their_entry = their_snapshot.get(path)
        entry, conflict = _merge_entry(
            repo, path, base_entry, our_entry, their_entry, theirs,
        )
        if conflict is not None:
            conflicts.append(conflict)
            changed.add(path)
            continue
        if entry is not None:
            pending[path] = entry
            merged_identity: Optional[Entry] = (entry.object_id(), entry.mode)
        else:
            merged_identity = None
        if merged_identity != our_entry:
            changed.add(path)

    for path in sorted(_directory_file_conflicts(pending)):
        if not any(item.path == path for item in conflicts):
            conflicts.append(
                _conflict(
                    path,
                    "directory/file",
                    base_snapshot.get(path),
                    our_snapshot.get(path),
                    their_snapshot.get(path),
                )
            )
        changed.add(path)

    conflicts.sort(key=lambda item: (item.path, item.reason))
    if conflicts:
        tree_oid = None
    else:
        tree_oid = _write_snapshot_tree(repo, _materialize_snapshot(repo, pending))

    return MergeTreeResult(
        tree_oid=tree_oid,
        base_oid=base_oid,
        ours_oid=ours_oid,
        theirs_oid=theirs_oid,
        conflicts=tuple(conflicts),
        changed_paths=tuple(sorted(changed)),
    )
