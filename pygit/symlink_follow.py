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
    """Result of following one ``TREEISH:path`` expression.

    ``status == "object"`` carries the resolved object ID. Other statuses use
    ``payload`` for Git's special batch response body (outside path or original
    expression for dangling/loop/notdir).
    """

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


def _lookup_entry(repo: Repository, root_oid: str, parts: List[str]) -> Tuple[Optional[object], Optional[str]]:
    """Return the final tree entry, or a failure kind while walking ``parts``."""

    current_oid = root_oid
    for index, part in enumerate(parts):
        tree = repo.store.read(current_oid)
        if not isinstance(tree, TreeObject):
            return None, "notdir"
        entry = next((item for item in tree.entries if item.name == part), None)
        if entry is None:
            return None, "missing"
        if index == len(parts) - 1:
            return entry, None
        if not entry.is_dir:
            return entry, "notdir"
        current_oid = entry.sha.lower()
    return None, "missing"


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

    Non-path expressions fall back to ordinary object resolution. Symlinks are
    followed only inside the selected tree snapshot; absolute targets or
    relative targets that escape above the snapshot root produce ``symlink``
    results instead of touching the host filesystem.
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

    pending = [part for part in raw_path.split("/") if part not in {"", "."}]
    if any(part == ".." for part in pending):
        raise ValueError(f"Invalid object path: {expression!r}")

    followed = 0
    seen_states: Set[Tuple[str, ...]] = set()

    while True:
        state = tuple(pending)
        if state in seen_states:
            return SymlinkResolution("loop", payload=expression)
        seen_states.add(state)

        entry, failure = _lookup_entry(repo, root_oid, pending)
        if failure == "missing":
            return SymlinkResolution("dangling", payload=expression)
        if failure == "notdir":
            return SymlinkResolution("notdir", payload=expression)
        assert entry is not None

        # Locate the final entry again while retaining its parent path so a
        # relative symlink target can be interpreted in tree coordinates.
        current_oid = root_oid
        parent: List[str] = []
        final = None
        for index, part in enumerate(pending):
            tree = repo.store.read(current_oid)
            assert isinstance(tree, TreeObject)
            final = next(item for item in tree.entries if item.name == part)
            if index == len(pending) - 1:
                break
            parent.append(part)
            current_oid = final.sha.lower()
        assert final is not None

        if not final.is_symlink:
            return SymlinkResolution("object", oid=final.sha.lower())

        followed += 1
        if followed > max_symlinks:
            return SymlinkResolution("loop", payload=expression)

        target_obj = repo.store.read(final.sha)
        if not isinstance(target_obj, BlobObject):
            raise RuntimeError(f"Symlink {final.sha} does not reference a blob")
        try:
            target = target_obj.data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("symlink target is not valid UTF-8") from exc

        resolved, outside = _normalize_relative(parent, target)
        if outside is not None:
            return SymlinkResolution("symlink", payload=outside)
        assert resolved is not None
        pending = resolved
        if not pending:
            return SymlinkResolution("object", oid=root_oid)
