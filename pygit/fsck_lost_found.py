"""Recovery materialization for ``fsck --lost-found``.

The fsck engine owns reachability classification.  This module only turns the
already-computed dangling set into Git-style recovery files without changing
object reachability or repository references.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence, Tuple

from .objects import BlobObject, CommitObject, GitObject
from .repo import Repository


@dataclass(frozen=True)
class LostFoundRecord:
    oid: str
    kind: str
    path: Path


def _recovery_payload(obj: GitObject, oid: str) -> Tuple[str, bytes]:
    if isinstance(obj, CommitObject):
        return "commit", (oid + "\n").encode("ascii")
    if isinstance(obj, BlobObject):
        return "other", obj.data
    return "other", (oid + "\n").encode("ascii")


def _ensure_safe_directory(path: Path) -> None:
    if path.exists():
        if path.is_symlink() or not path.is_dir():
            raise RuntimeError(f"lost-found path is not a safe directory: {path}")
        return
    parent = path.parent
    if parent.exists() and (parent.is_symlink() or not parent.is_dir()):
        raise RuntimeError(f"lost-found parent is not a safe directory: {parent}")
    path.mkdir(mode=0o700, parents=False, exist_ok=False)


def _atomic_write(path: Path, payload: bytes) -> None:
    if path.exists() and path.is_symlink():
        raise RuntimeError(f"refusing to replace symlink recovery file: {path}")
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise


def write_lost_found(repo: Repository, dangling_oids: Sequence[str]) -> Tuple[LostFoundRecord, ...]:
    """Write Git-style recovery files for dangling objects.

    All objects are read before filesystem mutation so unreadable dangling
    objects cannot leave a partially-created recovery set.  Commits are written
    below ``lost-found/commit``.  Trees, tags, and blobs use ``lost-found/other``;
    blobs contain their raw payload while every other recovery file contains the
    object's hexadecimal ID plus a trailing newline.
    """

    prepared: List[Tuple[str, str, bytes]] = []
    for raw_oid in sorted(set(dangling_oids)):
        oid = raw_oid.lower()
        obj = repo.store.read(oid)
        kind, payload = _recovery_payload(obj, oid)
        prepared.append((oid, kind, payload))

    root = repo.pygit_dir / "lost-found"
    if root.exists() and (root.is_symlink() or not root.is_dir()):
        raise RuntimeError(f"lost-found path is not a safe directory: {root}")
    if prepared and not root.exists():
        root.mkdir(mode=0o700, parents=False, exist_ok=False)

    needed = sorted({kind for _, kind, _ in prepared})
    for kind in needed:
        _ensure_safe_directory(root / kind)

    records: List[LostFoundRecord] = []
    for oid, kind, payload in prepared:
        path = root / kind / oid
        _atomic_write(path, payload)
        records.append(LostFoundRecord(oid=oid, kind=kind, path=path))
    return tuple(records)
