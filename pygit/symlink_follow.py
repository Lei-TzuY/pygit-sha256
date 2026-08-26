"""Tree-snapshot symlink resolution for ``cat-file --follow-symlinks``."""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from typing import List, Optional, Set, Tuple

from .objects import BlobObject, CommitObject, TagObject, TreeObject
from .repo import Repository
from .revision import resolve_revision


@dataclass(frozen=True)
class SymlinkResolution:
    """Result of following one ``TREEISH:path`` expression."""

    status: str
    oid: Optional[str] = None
    payload: Optional[str] = None


def _treeish_root(repo: Repository, expression: str) -> str:
    oid = resolve_revision(repo, expression)
    seen: Set[str] = set()
    while True:
        if oid in seen:
            raise RuntimeError(f"Tag cycle while resolving {expression!r}")
        seen.add(oid)
        obj = repo.store.read(oid)
        if isinstance(obj, TreeObject):
            return oid
        if isinstance(obj, CommitObject):
            tree = repo.store.read(obj.tree)
            if not isinstance(tree, TreeObject):
                raise RuntimeError(f"Commit {oid} references a non-tree root")
            return obj.tree.lower()
        if isinstance(obj, TagObject):
            oid = obj.target_sha.lower()
            continue
        raise RuntimeError(f"Object {expression!r} is not a tree-ish")


def _normalize_relative(parent: List[str], target: str) -> Tuple[Optional[List[str]], Optional[str]]:
    """Resolve a symlink target against its parent without escaping tree root."""

    if target.startswith("/"):
        return None, posixpath.normpath(target)

    combined = list(parent)
    escaped: List[str] = []
    for component in target.split("/"):
        if component in {"", "."}:
            continue
        if component == "..":
            if combined:
                combined.pop()
            else:
                escaped.append("..")
            continue
        if escaped:
            escaped.append(component)
        else:
            combined.append(component)

    if escaped:
        return None, "/".join(escaped)
    return combined, None


def resolve_following_symlinks(
    repo: Repository,
    expression: str,
    *,
    max_symlinks: int = 40,
) -> SymlinkResolution:
    """Resolve Git-style ``TREEISH:path`` while following in-tree symlinks.

    Resolution is confined to the selected tree object. The host filesystem is
    never consulted. Absolute targets and relative targets that escape above
    the tree root become Git-style ``symlink`` results. Missing paths after at
    least one symlink hop become ``dangling``; an initially missing path remains
    an ordinary ``missing`` record.
    """

    if ":" not in expression:
        return SymlinkResolution("object", oid=resolve_revision(repo, expression))

    base, raw_path = expression.split(":", 1)
    if not base:
        raise ValueError("index-style :path expressions are not supported")
    if raw_path.startswith("/") or "\x00" in raw_path:
        raise ValueError(f"Invalid object path: {expression!r}")

    root_oid = _treeish_root(repo, base)
    if raw_path == "":
        return SymlinkResolution("object", oid=root_oid)

    pending = raw_path.split("/")
    if any(part in {"", ".", ".."} for part in pending):
        raise ValueError(f"Invalid object path: {expression!r}")

    followed = 0
    seen_states: Set[Tuple[str, ...]] = set()

    while True:
        state = tuple(pending)
        if state in seen_states:
            return SymlinkResolution("loop", payload=expression)
        seen_states.add(state)

        current_oid = root_oid
        parent: List[str] = []
        restarted = False

        for index, part in enumerate(pending):
            tree = repo.store.read(current_oid)
            if not isinstance(tree, TreeObject):
                return SymlinkResolution("notdir", payload=expression)

            entry = next((item for item in tree.entries if item.name == part), None)
            if entry is None:
                status = "dangling" if followed else "missing"
                return SymlinkResolution(status, payload=expression)

            remaining = pending[index + 1 :]
            if entry.is_symlink:
                followed += 1
                if followed > max_symlinks:
                    return SymlinkResolution("loop", payload=expression)

                target_obj = repo.store.read(entry.sha)
                if not isinstance(target_obj, BlobObject):
                    raise RuntimeError(f"Symlink {entry.sha} does not reference a blob")
                try:
                    target = target_obj.data.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise ValueError("symlink target is not valid UTF-8") from exc

                resolved, outside = _normalize_relative(parent, target)
                if outside is not None:
                    if remaining:
                        outside = posixpath.normpath(posixpath.join(outside, *remaining))
                    return SymlinkResolution("symlink", payload=outside)

                assert resolved is not None
                pending = resolved + remaining
                if not pending:
                    return SymlinkResolution("object", oid=root_oid)
                restarted = True
                break

            if not remaining:
                return SymlinkResolution("object", oid=entry.sha.lower())
            if not entry.is_dir:
                return SymlinkResolution("notdir", payload=expression)

            parent.append(part)
            current_oid = entry.sha.lower()

        if not restarted:
            raise AssertionError("unreachable symlink traversal state")
