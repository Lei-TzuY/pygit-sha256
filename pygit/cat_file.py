"""Advanced ``cat-file`` plumbing for object inspection and batch queries.

The legacy CLI already supports single-object ``-t``, ``-s`` and ``-p`` modes.
This module adds the high-leverage plumbing modes used by scripts: existence
checks, stdin batch queries, abbreviated/ref object names, and ``REV:path``
lookups inside commit/tree snapshots.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from .objects import CommitObject, GitObject, TagObject, TreeObject
from .plumbing import resolve_commit
from .repo import Repository


@dataclass(frozen=True)
class CatFileRecord:
    expression: str
    oid: str
    type_name: str
    size: int
    content: bytes


def _resolve_plain(repo: Repository, expression: str) -> str:
    """Resolve a ref, full/prefix object ID, or commit-ish expression."""
    oid = repo.refs.resolve(expression)
    if oid and repo.store.exists(oid):
        return oid.lower()

    oid = repo.store.resolve_prefix(expression)
    if oid:
        return oid.lower()

    # Commit ancestry expressions such as HEAD~2 and topic^2 are handled by
    # the graph plumbing. Keep this last so a literal ref/object wins first.
    try:
        return resolve_commit(repo, expression)
    except (KeyError, ValueError, RuntimeError):
        raise KeyError(f"Unknown object: {expression!r}") from None


def _treeish(repo: Repository, oid: str, display: str) -> TreeObject:
    """Peel tags/commits until *oid* yields a tree for path traversal."""
    current = oid
    seen = set()
    while True:
        if current in seen:
            raise RuntimeError(f"Tag cycle while resolving {display!r}")
        seen.add(current)
        obj = repo.store.read(current)
        if isinstance(obj, TreeObject):
            return obj
        if isinstance(obj, CommitObject):
            tree = repo.store.read(obj.tree_sha)
            if not isinstance(tree, TreeObject):
                raise RuntimeError(f"Commit {current} references a non-tree root")
            return tree
        if isinstance(obj, TagObject):
            current = obj.target_sha
            continue
        raise RuntimeError(f"Object {display!r} is not a tree-ish")


def resolve_object(repo: Repository, expression: str) -> str:
    """Resolve an object-ish expression, including ``REV:path`` tree walks."""
    if not expression:
        raise ValueError("empty object expression")

    if ":" not in expression:
        return _resolve_plain(repo, expression)

    base, path = expression.split(":", 1)
    if not base:
        raise ValueError("index-style :path expressions are not supported")
    oid = _resolve_plain(repo, base)
    if not path:
        obj = repo.store.read(oid)
        if isinstance(obj, CommitObject):
            return obj.tree_sha
        if isinstance(obj, TagObject):
            tree = _treeish(repo, oid, expression)
            return tree.hash()
        return oid

    tree = _treeish(repo, oid, expression)
    parts = [part for part in path.split("/") if part]
    if not parts:
        raise ValueError(f"invalid object path: {expression!r}")

    current_tree = tree
    for index, part in enumerate(parts):
        entry = next((item for item in current_tree.entries if item.name == part), None)
        if entry is None:
            raise KeyError(f"Path {path!r} does not exist in {base!r}")
        if index == len(parts) - 1:
            return entry.sha.lower()
        obj = repo.store.read(entry.sha)
        if not isinstance(obj, TreeObject):
            raise KeyError(f"Path component {part!r} is not a directory")
        current_tree = obj

    raise AssertionError("unreachable")


def inspect_object(repo: Repository, expression: str) -> CatFileRecord:
    oid = resolve_object(repo, expression)
    obj: GitObject = repo.store.read(oid)
    content = obj.serialize()
    return CatFileRecord(
        expression=expression,
        oid=oid,
        type_name=obj.type_name.decode("ascii"),
        size=len(content),
        content=content,
    )


def object_exists(repo: Repository, expression: str) -> bool:
    try:
        inspect_object(repo, expression)
        return True
    except (KeyError, ValueError, RuntimeError):
        return False


def batch_records(repo: Repository, expressions: Iterable[str]) -> Iterable[Optional[CatFileRecord]]:
    """Inspect each input independently; missing/malformed names yield ``None``."""
    for raw in expressions:
        expression = raw.rstrip("\r\n")
        if not expression:
            yield None
            continue
        try:
            yield inspect_object(repo, expression)
        except (KeyError, ValueError, RuntimeError):
            yield None
