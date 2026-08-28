"""Stable native-parent identity support for shallow imported commits.

A real shallow pack may contain a commit while omitting one or more of that
commit's native parents.  pygit cannot translate an unavailable native SHA-1
parent to a local SHA-256 commit id at import time without either inventing an
object id or later rewriting the child commit.

Phase204 therefore gives imported shallow commits a stable local representation:
the commit payload stores its original native SHA-1 parent ids in
``parent-sha1`` headers.  The commit itself remains a normal content-addressed
SHA-256 pygit object.  This small repository-side index maps native commit ids
to the local SHA-256 commit ids that have actually arrived so ObjectStore can
resolve those parent edges lazily as a repository is deepened.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Dict, Iterable, List


_FILE_NAME = "foreign-commits.json"


def _path(pygit_dir: Path) -> Path:
    return Path(pygit_dir) / _FILE_NAME


def read_foreign_commit_map(pygit_dir: Path) -> Dict[str, str]:
    """Return ``native SHA-1 -> local SHA-256`` mappings for imported commits."""
    path = _path(pygit_dir)
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("malformed foreign commit map")

    result: Dict[str, str] = {}
    for native, local in raw.items():
        native_s = str(native).lower()
        local_s = str(local).lower()
        if len(native_s) != 40 or len(local_s) != 64:
            raise ValueError("malformed foreign commit map object id")
        try:
            int(native_s, 16)
            int(local_s, 16)
        except ValueError as exc:
            raise ValueError("malformed foreign commit map object id") from exc
        result[native_s] = local_s
    return result


def write_foreign_commit_map(pygit_dir: Path, mapping: Dict[str, str]) -> None:
    """Atomically replace the foreign commit identity index."""
    pygit_dir = Path(pygit_dir)
    path = _path(pygit_dir)
    if not mapping:
        if path.exists():
            path.unlink()
        return

    payload = json.dumps(dict(sorted(mapping.items())), indent=2, sort_keys=True) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix="foreign-commits-", dir=str(pygit_dir))
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def update_foreign_commit_map(pygit_dir: Path, additions: Dict[str, str]) -> None:
    """Merge newly imported native commit identities into the persistent map."""
    if not additions:
        return
    current = read_foreign_commit_map(pygit_dir)
    for native, local in additions.items():
        native_s = native.lower()
        local_s = local.lower()
        previous = current.get(native_s)
        if previous is not None and previous != local_s:
            raise RuntimeError(
                f"native commit {native_s} already maps to a different local object"
            )
        current[native_s] = local_s
    write_foreign_commit_map(pygit_dir, current)


def resolve_native_parents(pygit_dir: Path, native_parents: Iterable[str]) -> List[str]:
    """Resolve all native parents or expose none while a boundary is incomplete.

    Returning no parents until every direct parent is available preserves Git's
    shallow-boundary semantics for merge commits and avoids accidentally turning
    a resolved second parent into a synthetic first parent.
    """
    parents = list(native_parents)
    if not parents:
        return []
    mapping = read_foreign_commit_map(pygit_dir)
    resolved = [mapping.get(native.lower()) for native in parents]
    if any(local is None for local in resolved):
        return []
    return [str(local) for local in resolved]
