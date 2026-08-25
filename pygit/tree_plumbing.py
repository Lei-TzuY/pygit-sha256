"""
Tree-object and index plumbing helpers.

This module backs ``mktree`` and ``read-tree`` while keeping low-level object /
index operations out of the large porcelain modules. Object IDs are pygit's
native 64-hex SHA-256 values.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .index import IndexEntry
from .objects import BlobObject, CommitObject, TagObject, TreeEntry, TreeObject
from .repo import Repository


_HEX = frozenset("0123456789abcdef")
_ALLOWED_MODES = {"040000", "100644", "100755", "120000", "160000"}


@dataclass(frozen=True)
class ParsedTreeEntry:
    mode: str
    object_type: str
    oid: str
    name: str


def _is_oid(value: str) -> bool:
    return len(value) == 64 and all(ch in _HEX for ch in value.lower())


def _expected_type(mode: str) -> str:
    if mode == "040000":
        return "tree"
    if mode == "160000":
        return "commit"
    return "blob"


def _object_type(repo: Repository, oid: str) -> str:
    obj = repo.store.read(oid)
    return obj.type_name.decode("ascii", "replace")


def _validate_entry_name(name: str) -> None:
    if not name:
        raise ValueError("tree entry name must not be empty")
    if name in {".", ".."}:
        raise ValueError(f"invalid tree entry name: {name!r}")
    if "/" in name or "\x00" in name:
        raise ValueError(f"tree entry name must be a single path component: {name!r}")


def parse_mktree_record(record: str) -> ParsedTreeEntry:
    """Parse ``MODE TYPE OID<TAB>NAME`` input accepted by ``mktree``."""
    metadata, sep, name = record.partition("\t")
    if not sep:
        raise ValueError("mktree input must contain a tab before the entry name")
    parts = metadata.split()
    if len(parts) != 3:
        raise ValueError("mktree input must be: <mode> <type> <object>\\t<name>")

    mode, object_type, oid = parts
    if mode not in _ALLOWED_MODES:
        raise ValueError(f"unsupported tree mode: {mode!r}")
    if object_type not in {"blob", "tree", "commit"}:
        raise ValueError(f"unsupported tree object type: {object_type!r}")
    if object_type != _expected_type(mode):
        raise ValueError(
            f"mode {mode} requires object type {_expected_type(mode)!r}, "
            f"not {object_type!r}"
        )
    if not _is_oid(oid):
        raise ValueError("tree object ID must be a 64-hex SHA-256 value")
    _validate_entry_name(name)
    return ParsedTreeEntry(mode, object_type, oid.lower(), name)


def make_tree(
    repo: Repository,
    records: Sequence[str],
    *,
    missing: bool = False,
) -> str:
    """Create and store a tree object from textual entry records."""
    parsed = [parse_mktree_record(record) for record in records if record != ""]
    names: Set[str] = set()
    entries: List[TreeEntry] = []

    for item in parsed:
        if item.name in names:
            raise ValueError(f"duplicate tree entry name: {item.name!r}")
        names.add(item.name)

        if repo.store.exists(item.oid):
            actual_type = _object_type(repo, item.oid)
            if actual_type != item.object_type:
                raise ValueError(
                    f"object {item.oid} is {actual_type!r}, expected {item.object_type!r}"
                )
        elif not missing:
            raise KeyError(f"Object not found: {item.oid}")

        entries.append(TreeEntry(mode=item.mode, name=item.name, sha=item.oid))

    return repo.store.write(TreeObject(entries))


def _resolve_object_id(repo: Repository, name: str) -> str:
    oid = repo.refs.resolve(name)
    if oid:
        return oid

    if name.startswith("refs/"):
        refs_root = (repo.pygit_dir / "refs").resolve()
        path = (repo.pygit_dir / name).resolve()
        try:
            path.relative_to(refs_root)
        except ValueError as exc:
            raise ValueError(f"invalid ref name: {name!r}") from exc
        if path.is_file():
            value = path.read_text(encoding="utf-8").strip()
            if not _is_oid(value):
                raise RuntimeError(f"malformed ref {name}: expected a 64-hex object ID")
            return value

    oid = repo.store.resolve_prefix(name)
    if oid:
        return oid
    raise KeyError(f"Unknown tree-ish: {name!r}")


def resolve_treeish(repo: Repository, treeish: str) -> str:
    """Resolve a tree, commit, or annotated tag naming a tree/commit to a tree OID."""
    # Parent-walking revision expressions are necessarily commit-ish.
    if "~" in treeish or "^" in treeish:
        from .plumbing import resolve_commit

        commit_oid = resolve_commit(repo, treeish)
        obj = repo.store.read(commit_oid)
        assert isinstance(obj, CommitObject)
        return obj.tree

    oid = _resolve_object_id(repo, treeish)
    seen: Set[str] = set()
    while True:
        if oid in seen:
            raise RuntimeError(f"tag cycle while resolving {treeish!r}")
        seen.add(oid)
        obj = repo.store.read(oid)
        if isinstance(obj, TreeObject):
            return oid
        if isinstance(obj, CommitObject):
            return obj.tree
        if isinstance(obj, TagObject):
            oid = obj.target_sha
            continue
        raise ValueError(f"tree-ish {treeish!r} resolves to a non-tree object")


def flatten_tree(repo: Repository, tree_oid: str) -> Dict[str, Tuple[str, str]]:
    """Flatten a tree recursively into ``path -> (oid, mode)`` entries."""
    result: Dict[str, Tuple[str, str]] = {}

    def visit(oid: str, prefix: str) -> None:
        obj = repo.store.read(oid)
        if not isinstance(obj, TreeObject):
            raise ValueError(f"object {oid} referenced as a tree is not a tree")
        for entry in obj.entries:
            _validate_entry_name(entry.name)
            path = entry.name if not prefix else f"{prefix}/{entry.name}"
            if entry.mode == "040000":
                visit(entry.sha, path)
                continue
            if entry.mode not in _ALLOWED_MODES:
                raise ValueError(f"unsupported tree mode {entry.mode!r} at {path!r}")
            expected = _expected_type(entry.mode)
            actual = _object_type(repo, entry.sha)
            if actual != expected:
                raise ValueError(
                    f"tree entry {path!r} expects {expected!r}, object is {actual!r}"
                )
            result[path] = (entry.sha, entry.mode)

    visit(tree_oid, "")
    return result


def _index_entry(repo: Repository, path: str, oid: str, mode: str) -> IndexEntry:
    worktree_path = repo.worktree / path
    if worktree_path.exists() or worktree_path.is_symlink():
        stat = worktree_path.lstat()
        return IndexEntry(path, oid, mode, stat.st_size, stat.st_mtime)

    if mode == "160000":
        size = 0
    else:
        obj = repo.store.read(oid)
        size = len(obj.serialize()) if isinstance(obj, BlobObject) else 0
    return IndexEntry(path, oid, mode, size, 0.0)


def _normalized_prefix(prefix: Optional[str]) -> Optional[str]:
    if prefix is None:
        return None
    value = prefix.strip("/")
    if not value:
        raise ValueError("--prefix must not be empty")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"invalid read-tree prefix: {prefix!r}")
    return value


def read_tree(
    repo: Repository,
    treeish: Optional[str] = None,
    *,
    empty: bool = False,
    prefix: Optional[str] = None,
    update_worktree: bool = False,
) -> List[IndexEntry]:
    """
    Load a tree into the index.

    By default the index is replaced. ``--prefix`` adds the tree beneath a
    path prefix without disturbing existing entries. ``-u`` also updates the
    working tree, but only from a clean repository to avoid clobbering changes.
    """
    normalized_prefix = _normalized_prefix(prefix)
    if empty and treeish is not None:
        raise ValueError("--empty cannot be combined with a tree-ish")
    if empty and normalized_prefix is not None:
        raise ValueError("--empty cannot be combined with --prefix")
    if not empty and treeish is None:
        raise ValueError("read-tree requires a tree-ish or --empty")

    old_paths = set(repo.index.paths())
    if update_worktree:
        repo._ensure_clean_worktree("read-tree -u")

    if empty:
        entries: Dict[str, IndexEntry] = {}
        tree_oid = None
        flattened: Dict[str, Tuple[str, str]] = {}
    else:
        assert treeish is not None
        tree_oid = resolve_treeish(repo, treeish)
        flattened = flatten_tree(repo, tree_oid)

        if normalized_prefix is None:
            entries = {
                path: _index_entry(repo, path, oid, mode)
                for path, (oid, mode) in flattened.items()
            }
        else:
            entries = dict(repo.index.entries)
            additions: Dict[str, IndexEntry] = {}
            for path, (oid, mode) in flattened.items():
                indexed_path = f"{normalized_prefix}/{path}"
                if indexed_path in entries:
                    raise RuntimeError(f"read-tree --prefix would overwrite {indexed_path!r}")
                additions[indexed_path] = _index_entry(repo, indexed_path, oid, mode)
            # Prevent path/file conflicts such as existing 'vendor' + new 'vendor/x'.
            new_paths = set(additions)
            all_paths = set(entries) | new_paths
            for path in new_paths:
                parts = path.split("/")
                for index in range(1, len(parts)):
                    parent = "/".join(parts[:index])
                    if parent in all_paths:
                        raise RuntimeError(
                            f"read-tree --prefix path conflict between {parent!r} and {path!r}"
                        )
            entries.update(additions)

    if update_worktree:
        if normalized_prefix is None:
            target_paths = set(entries)
            for path in old_paths - target_paths:
                repo._remove_worktree_file(path)
            if tree_oid is not None:
                repo._restore_tree(tree_oid, repo.worktree)
        else:
            assert tree_oid is not None
            destination = repo.worktree / normalized_prefix
            destination.mkdir(parents=True, exist_ok=True)
            repo._restore_tree(tree_oid, destination)

        # Refresh filesystem metadata after materialization/removal.
        entries = {
            path: _index_entry(repo, path, entry.sha, entry.mode)
            for path, entry in entries.items()
        }

    repo.index.entries = entries
    repo.index.save()
    return repo.index.all_entries()
