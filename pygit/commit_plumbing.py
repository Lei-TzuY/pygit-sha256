"""Low-level tree/commit construction plumbing.

This module backs ``write-tree`` and ``commit-tree``.  It deliberately does
not update refs or run porcelain hooks: both commands only create immutable
objects and print their SHA-256 object IDs.
"""

from __future__ import annotations

import datetime as _datetime
import os
from pathlib import Path
from typing import Mapping, Optional, Sequence

from .index import IndexEntry
from .objects import BlobObject, CommitObject, TreeObject
from .objects.commit import Identity
from .plumbing import resolve_commit
from .repo import Repository


_HEX = frozenset("0123456789abcdef")
_FILE_MODES = {"100644", "100755", "120000"}
_INDEX_MODES = _FILE_MODES | {"160000"}


def _is_oid(value: str) -> bool:
    return len(value) == 64 and all(ch in _HEX for ch in value.lower())


def _validate_index_path(path: str) -> None:
    if not path or path.startswith("/") or "\x00" in path:
        raise ValueError(f"invalid index path: {path!r}")
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"invalid index path: {path!r}")
    if parts[0] == ".pygit":
        raise ValueError("index must not contain pygit internal metadata")


def _validate_index_entries(
    repo: Repository,
    entries: Sequence[IndexEntry],
    *,
    missing_ok: bool,
) -> None:
    paths = {entry.path for entry in entries}
    for entry in entries:
        _validate_index_path(entry.path)
        if entry.mode not in _INDEX_MODES:
            raise ValueError(f"unsupported index mode {entry.mode!r} at {entry.path!r}")
        if not _is_oid(entry.sha):
            raise ValueError(f"index entry {entry.path!r} has an invalid SHA-256 object ID")

        parts = entry.path.split("/")
        for i in range(1, len(parts)):
            parent = "/".join(parts[:i])
            if parent in paths:
                raise ValueError(
                    f"index path conflict between {parent!r} and {entry.path!r}"
                )

        if not repo.store.exists(entry.sha):
            if missing_ok:
                continue
            raise KeyError(f"Object not found: {entry.sha}")

        obj = repo.store.read(entry.sha)
        if entry.mode == "160000":
            if not isinstance(obj, CommitObject):
                raise ValueError(
                    f"index entry {entry.path!r} uses gitlink mode but does not reference a commit"
                )
        elif not isinstance(obj, BlobObject):
            raise ValueError(
                f"index entry {entry.path!r} with mode {entry.mode} must reference a blob"
            )


def _normalize_prefix(prefix: Optional[str]) -> Optional[str]:
    if prefix is None:
        return None
    value = prefix.strip("/")
    if not value:
        raise ValueError("--prefix must not be empty")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"invalid write-tree prefix: {prefix!r}")
    return value


def write_tree(
    repo: Repository,
    *,
    missing_ok: bool = False,
    prefix: Optional[str] = None,
) -> str:
    """Write the current index as tree objects and return the root tree OID.

    ``prefix`` selects only entries beneath that directory and strips the
    prefix before building the returned subtree.  The index itself is never
    modified.
    """
    entries = list(repo.index.all_entries())
    _validate_index_entries(repo, entries, missing_ok=missing_ok)

    normalized = _normalize_prefix(prefix)
    if normalized is not None:
        needle = normalized + "/"
        selected = [entry for entry in entries if entry.path.startswith(needle)]
        if not selected:
            raise KeyError(f"no index entries beneath prefix {normalized!r}")
        entries = [
            IndexEntry(
                path=entry.path[len(needle):],
                sha=entry.sha,
                mode=entry.mode,
                size=entry.size,
                mtime=entry.mtime,
            )
            for entry in selected
        ]
        _validate_index_entries(repo, entries, missing_ok=missing_ok)

    return repo._build_tree_from_entries(entries)


def _resolve_tree_oid(repo: Repository, value: str) -> str:
    oid = value.lower()
    if not _is_oid(oid):
        resolved = repo.store.resolve_prefix(value)
        if resolved is None:
            raise KeyError(f"Unknown tree object: {value!r}")
        oid = resolved
    if not repo.store.exists(oid):
        raise KeyError(f"Object not found: {oid}")
    obj = repo.store.read(oid)
    if not isinstance(obj, TreeObject):
        raise ValueError(f"commit-tree requires a tree object, got {obj.type_name.decode()!r}")
    return oid


def _parse_identity_date(value: Optional[str]) -> tuple[int, str]:
    if not value:
        return int(_datetime.datetime.now(tz=_datetime.timezone.utc).timestamp()), "+0000"

    raw = value.strip()
    parts = raw.rsplit(" ", 1)
    timezone = "+0000"
    date_part = raw
    if len(parts) == 2 and len(parts[1]) == 5 and parts[1][0] in "+-" and parts[1][1:].isdigit():
        date_part, timezone = parts

    try:
        timestamp = int(date_part)
    except ValueError:
        dt = _datetime.datetime.fromisoformat(date_part)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_datetime.timezone.utc)
        timestamp = int(dt.timestamp())
        if timezone == "+0000" and dt.utcoffset() is not None:
            total_minutes = int(dt.utcoffset().total_seconds() // 60)
            sign = "+" if total_minutes >= 0 else "-"
            total_minutes = abs(total_minutes)
            timezone = f"{sign}{total_minutes // 60:02d}{total_minutes % 60:02d}"
    return timestamp, timezone


def _identity(
    env: Mapping[str, str],
    role: str,
    *,
    fallback_name: Optional[str] = None,
    fallback_email: Optional[str] = None,
) -> Identity:
    prefix = f"GIT_{role}_"
    name = env.get(prefix + "NAME") or fallback_name or "Unknown"
    email = env.get(prefix + "EMAIL") or fallback_email or "unknown@example.com"
    timestamp, timezone = _parse_identity_date(env.get(prefix + "DATE"))
    return Identity(name=name, email=email, timestamp=timestamp, timezone=timezone)


def commit_tree(
    repo: Repository,
    tree: str,
    *,
    parents: Sequence[str] = (),
    message: str = "",
    env: Optional[Mapping[str, str]] = None,
) -> str:
    """Create a commit object without updating HEAD or any branch ref."""
    tree_oid = _resolve_tree_oid(repo, tree)

    parent_oids = []
    for parent in parents:
        try:
            oid = resolve_commit(repo, parent)
        except RuntimeError as exc:
            raise ValueError(f"invalid parent {parent!r}: {exc}") from exc
        if oid in parent_oids:
            raise ValueError(f"duplicate parent commit: {parent!r}")
        parent_oids.append(oid)

    source = os.environ if env is None else env
    author = _identity(source, "AUTHOR")
    committer = _identity(
        source,
        "COMMITTER",
        fallback_name=author.name,
        fallback_email=author.email,
    )

    obj = CommitObject(
        tree=tree_oid,
        parents=parent_oids,
        author=author,
        committer=committer,
        message=message,
    )
    return repo.store.write(obj)


def read_message_file(path: str) -> str:
    """Read a UTF-8 commit message file for ``commit-tree -F``."""
    return Path(path).read_text(encoding="utf-8")
