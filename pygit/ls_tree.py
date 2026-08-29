"""Typed, revision-aware tree inspection for ``pygit ls-tree``.

The implementation is intentionally read-only.  It uses the shared revision
resolver, understands pygit's SHA-256 tree modes, and keeps traversal separate
from presentation so callers can inspect structured records directly.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Match, Optional, Sequence, Set, Tuple

from .objects import CommitObject, TagObject, TreeObject
from .promisor import promised_kind, read_promisor_state
from .promisor_materialize import materialize_promised_objects
from .repo import Repository
from .revision import abbreviate_oid, resolve_revision


_HEX = frozenset("0123456789abcdef")
_MODE_TYPE = {
    "040000": "tree",
    "100644": "blob",
    "100755": "blob",
    "120000": "blob",
    "160000": "commit",
}
_FORMAT_ATOM = re.compile(r"%\(([^)]+)\)")
_FORMAT_FIELDS = frozenset({"objectmode", "objecttype", "objectname", "path"})
_GLOB_MAGIC = frozenset("*?[")


@dataclass(frozen=True)
class LsTreeEntry:
    """One path reported by :func:`ls_tree`."""

    mode: str
    object_type: str
    oid: str
    path: str


def _treeish_oid(repo: Repository, expression: str) -> str:
    """Resolve *expression* to a concrete tree object ID."""
    current = resolve_revision(repo, expression)
    seen: Set[str] = set()
    while True:
        if current in seen:
            raise RuntimeError(f"tag cycle while resolving tree-ish {expression!r}")
        seen.add(current)
        obj = repo.store.read(current)
        if isinstance(obj, TreeObject):
            return current
        if isinstance(obj, CommitObject):
            tree_oid = obj.tree.lower()
            tree = repo.store.read(tree_oid)
            if not isinstance(tree, TreeObject):
                raise RuntimeError(f"commit {current} references a non-tree root")
            return tree_oid
        if isinstance(obj, TagObject):
            current = obj.target_sha.lower()
            continue
        raise RuntimeError(f"object {expression!r} is not a tree-ish")


def _validate_name(name: str) -> None:
    if not name or name in {".", ".."} or "/" in name or "\x00" in name:
        raise ValueError(f"invalid tree entry name: {name!r}")


def _validate_oid(oid: str) -> str:
    lowered = oid.lower()
    if len(lowered) != 64 or any(char not in _HEX for char in lowered):
        raise ValueError(f"invalid tree entry object ID: {oid!r}")
    return lowered


def _entry_type(mode: str) -> str:
    try:
        return _MODE_TYPE[mode]
    except KeyError as exc:
        raise ValueError(f"unsupported tree entry mode: {mode!r}") from exc


def _normalize_patterns(patterns: Sequence[str]) -> Tuple[str, ...]:
    normalized: List[str] = []
    for raw in patterns:
        if raw == "":
            raise ValueError("empty ls-tree pathspec")
        if raw.startswith("/") or "\\" in raw or "\x00" in raw:
            raise ValueError(f"invalid ls-tree pathspec: {raw!r}")
        value = raw.rstrip("/")
        if not value:
            raise ValueError(f"invalid ls-tree pathspec: {raw!r}")
        parts = value.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError(f"invalid ls-tree pathspec: {raw!r}")
        normalized.append(value)
    return tuple(normalized)


def _has_magic(pattern: str) -> bool:
    return any(char in _GLOB_MAGIC for char in pattern)


def _glob_prefix(pattern: str) -> str:
    positions = [pattern.find(char) for char in _GLOB_MAGIC if char in pattern]
    if not positions:
        return pattern
    return pattern[: min(positions)].rstrip("/")


def _matches(path: str, patterns: Tuple[str, ...]) -> bool:
    if not patterns:
        return True
    for pattern in patterns:
        if _has_magic(pattern):
            if fnmatch.fnmatchcase(path, pattern):
                return True
            continue
        if path == pattern or path.startswith(pattern + "/"):
            return True
    return False


def _may_descend(path: str, patterns: Tuple[str, ...]) -> bool:
    """Return whether a subtree can contain a selected pathspec match."""
    if not patterns:
        return True
    for pattern in patterns:
        if _has_magic(pattern):
            prefix = _glob_prefix(pattern)
            if not prefix or path.startswith(prefix) or prefix.startswith(path + "/"):
                return True
            continue
        if (
            pattern == path
            or pattern.startswith(path + "/")
            or path.startswith(pattern + "/")
        ):
            return True
    return False


def _needs_nonrecursive_descent(path: str, patterns: Tuple[str, ...]) -> bool:
    for pattern in patterns:
        if _has_magic(pattern):
            if "/" in pattern and _may_descend(path, (pattern,)):
                return True
            continue
        if pattern.startswith(path + "/"):
            return True
    return False


def _collect_ls_tree_promises(
    repo: Repository,
    root_oid: str,
    *,
    recursive: bool,
    directories_only: bool,
    patterns: Tuple[str, ...],
) -> Set[str]:
    """Collect unresolved blobs that the historical traversal will report.

    Foreign partial-clone trees retain native SHA-1 entry identities until a
    promised blob is materialized. ``ls_tree`` ultimately reports repository
    visible SHA-256 object names, so every selected unresolved blob must be
    materialized before the normal formatter can validate its object id. This
    planner mirrors the existing pathspec/descent rules without touching
    ``TreeEntry.sha`` for blobs, allowing the selected set to be fetched once.
    """
    promised: Set[str] = set()
    pending: List[Tuple[str, str, Set[str]]] = [(root_oid, "", set())]

    while pending:
        tree_oid, prefix, active = pending.pop()
        if tree_oid in active:
            raise RuntimeError("tree cycle while planning ls-tree promisor objects")
        tree = repo.store.read(tree_oid)
        if not isinstance(tree, TreeObject):
            raise RuntimeError(f"tree entry {tree_oid} does not reference a tree")
        next_active = set(active)
        next_active.add(tree_oid)

        for item in tree.entries:
            _validate_name(item.name)
            kind = _entry_type(item.mode)
            path = f"{prefix}/{item.name}" if prefix else item.name
            matched = _matches(path, patterns)

            if kind == "tree":
                if recursive:
                    descend = _may_descend(path, patterns)
                else:
                    descend = _needs_nonrecursive_descent(path, patterns)
                if descend:
                    pending.append((item.sha, path, next_active))
                continue

            if directories_only or not matched or item.is_resolved or not item.native_oid:
                continue
            if promised_kind(repo.pygit_dir, item.native_oid):
                promised.add(item.native_oid)

    return promised


def ls_tree(
    repo: Repository,
    treeish: str = "HEAD",
    *,
    recursive: bool = False,
    directories_only: bool = False,
    show_trees: bool = False,
    patterns: Sequence[str] = (),
) -> Tuple[LsTreeEntry, ...]:
    """Return structured entries below *treeish*.

    ``treeish`` may be a ref, full/abbreviated SHA-256 object ID, ancestry
    expression, annotated tag, tree object, or ``REV:path`` expression accepted
    by :func:`pygit.revision.resolve_revision`.

    Without ``recursive``, direct children are reported. A nested pathspec may
    still cause the minimum subtree traversal needed to reach that path. With
    recursion enabled, tree entries are omitted unless ``show_trees`` or
    ``directories_only`` is requested.
    """

    selected_patterns = _normalize_patterns(patterns)
    root_oid = _treeish_oid(repo, treeish)

    state = read_promisor_state(repo.pygit_dir)
    if state.get("promised"):
        promises = _collect_ls_tree_promises(
            repo,
            root_oid,
            recursive=recursive,
            directories_only=directories_only,
            patterns=selected_patterns,
        )
        if promises:
            materialize_promised_objects(repo.pygit_dir, sorted(promises))

    records: List[LsTreeEntry] = []

    def walk(tree_oid: str, prefix: str, active: Set[str]) -> None:
        if tree_oid in active:
            raise RuntimeError(f"tree cycle while traversing {treeish!r}")
        tree = repo.store.read(tree_oid)
        if not isinstance(tree, TreeObject):
            raise RuntimeError(f"tree entry {tree_oid} does not reference a tree")

        next_active = set(active)
        next_active.add(tree_oid)
        for item in sorted(tree.entries, key=lambda entry: entry.name):
            _validate_name(item.name)
            kind = _entry_type(item.mode)
            path = f"{prefix}/{item.name}" if prefix else item.name
            matched = _matches(path, selected_patterns)

            if kind == "tree":
                include_tree = matched and (not recursive or show_trees or directories_only)
                if recursive:
                    descend = _may_descend(path, selected_patterns)
                else:
                    descend = _needs_nonrecursive_descent(path, selected_patterns)
                if not include_tree and not descend:
                    continue

                oid = _validate_oid(item.sha)
                if include_tree:
                    records.append(LsTreeEntry(item.mode, kind, oid, path))

                if descend:
                    child = repo.store.read(oid)
                    if not isinstance(child, TreeObject):
                        raise RuntimeError(
                            f"tree entry {path!r} references non-tree object {oid}"
                        )
                    walk(oid, path, next_active)
                continue

            if not directories_only and matched:
                oid = _validate_oid(item.sha)
                records.append(LsTreeEntry(item.mode, kind, oid, path))

    walk(root_oid, "", set())
    return tuple(records)


def _format_template(template: str, values: Dict[str, str]) -> str:
    sentinel = "\x00PERCENT\x00"
    escaped = template.replace("%%", sentinel)

    def replace(match: Match[str]) -> str:
        field = match.group(1)
        if field not in _FORMAT_FIELDS:
            raise ValueError(f"unsupported ls-tree format atom: {field!r}")
        return values[field]

    rendered = _FORMAT_ATOM.sub(replace, escaped)
    return rendered.replace(sentinel, "%")


def format_ls_tree(
    repo: Repository,
    entries: Iterable[LsTreeEntry],
    *,
    name_only: bool = False,
    object_only: bool = False,
    format_string: Optional[str] = None,
    abbrev: Optional[int] = None,
    nul_terminated: bool = False,
) -> bytes:
    """Format structured entries using Git-like ``ls-tree`` records."""

    selected = int(name_only) + int(object_only) + int(format_string is not None)
    if selected > 1:
        raise ValueError("--name-only, --object-only, and --format are mutually exclusive")
    if abbrev is not None and (abbrev < 4 or abbrev > 64):
        raise ValueError("ls-tree abbreviation length must be between 4 and 64")

    lines: List[str] = []
    for entry in entries:
        object_name = entry.oid
        if abbrev is not None:
            if repo.store.exists(entry.oid):
                object_name = abbreviate_oid(repo, entry.oid, minimum=abbrev)
            else:
                object_name = entry.oid[:abbrev]

        if name_only:
            rendered = entry.path
        elif object_only:
            rendered = object_name
        else:
            template = (
                format_string
                if format_string is not None
                else "%(objectmode) %(objecttype) %(objectname)\t%(path)"
            )
            rendered = _format_template(
                template,
                {
                    "objectmode": entry.mode,
                    "objecttype": entry.object_type,
                    "objectname": object_name,
                    "path": entry.path,
                },
            )
        lines.append(rendered)

    if not lines:
        return b""
    separator = "\x00" if nul_terminated else "\n"
    return (separator.join(lines) + separator).encode("utf-8")
