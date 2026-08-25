"""
Index plumbing helpers backing ``update-index`` and ``ls-files``.

The on-disk index remains pygit's readable JSON format, but the behavior mirrors
Git's low-level staging primitives closely enough for scripts and tests to work
against the index without going through porcelain commands.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import stat
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .index import IndexEntry
from .objects import BlobObject, CommitObject
from .objects.base import HASH_ALGO
from .repo import Repository


_INDEX_MODES = {"100644", "100755", "120000", "160000"}


def _normalize_index_path(repo: Repository, path: str) -> str:
    if not path or path == ".":
        raise ValueError("index path must not be empty")

    raw = Path(path)
    root = Path(os.path.abspath(str(repo.worktree)))
    candidate = Path(os.path.abspath(str(raw if raw.is_absolute() else root / raw)))
    try:
        relative = candidate.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError(f"path is outside the repository: {path!r}") from exc

    if not relative or relative == ".":
        raise ValueError("index path must name a file")
    if relative == ".pygit" or relative.startswith(".pygit/"):
        raise ValueError("cannot add pygit's internal metadata to the index")
    if any(part in {"", ".", ".."} for part in relative.split("/")):
        raise ValueError(f"invalid index path: {path!r}")
    return relative


def _path_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _worktree_blob(path: Path) -> Tuple[bytes, str, int, float]:
    if path.is_symlink():
        data = os.readlink(path).encode("utf-8", "surrogateescape")
        st = path.lstat()
        return data, "120000", len(data), st.st_mtime
    if not path.is_file():
        raise ValueError(f"worktree path is not a regular file or symlink: {path}")

    data = path.read_bytes()
    st = path.stat()
    mode = "100755" if st.st_mode & stat.S_IXUSR else "100644"
    return data, mode, st.st_size, st.st_mtime


def _blob_oid(data: bytes) -> str:
    return hashlib.new(HASH_ALGO, BlobObject(data)._build_store_bytes()).hexdigest()


def _resolve_oid(repo: Repository, value: str) -> str:
    oid = repo.store.resolve_prefix(value)
    if oid:
        return oid
    raise KeyError(f"Object not found: {value}")


def _validate_cache_object(repo: Repository, mode: str, oid: str) -> None:
    if mode not in _INDEX_MODES:
        raise ValueError(f"unsupported index mode: {mode!r}")
    obj = repo.store.read(oid)
    if mode == "160000":
        if not isinstance(obj, CommitObject):
            raise ValueError(f"mode 160000 requires a commit object, got {obj.type_name!r}")
    elif not isinstance(obj, BlobObject):
        raise ValueError(f"mode {mode} requires a blob object, got {obj.type_name!r}")


def _entry_for_cache(repo: Repository, path: str, mode: str, oid: str) -> IndexEntry:
    target = repo.worktree / path
    if _path_exists(target):
        st = target.lstat()
        return IndexEntry(path, oid, mode, st.st_size, st.st_mtime)
    if mode == "160000":
        size = 0
    else:
        obj = repo.store.read(oid)
        size = len(obj.data) if isinstance(obj, BlobObject) else 0
    return IndexEntry(path, oid, mode, size, 0.0)


def _check_path_conflict(entries: Dict[str, IndexEntry], path: str) -> None:
    parts = path.split("/")
    for index in range(1, len(parts)):
        parent = "/".join(parts[:index])
        if parent in entries:
            raise RuntimeError(f"index path conflict between {parent!r} and {path!r}")
    prefix = path + "/"
    for existing in entries:
        if existing.startswith(prefix):
            raise RuntimeError(f"index path conflict between {path!r} and {existing!r}")


def parse_cache_info(repo: Repository, mode: str, object_name: str, path: str) -> IndexEntry:
    """Build one validated cache-info entry without mutating the index."""
    normalized = _normalize_index_path(repo, path)
    oid = _resolve_oid(repo, object_name)
    _validate_cache_object(repo, mode, oid)
    return _entry_for_cache(repo, normalized, mode, oid)


def parse_index_info(repo: Repository, record: str) -> Tuple[str, Optional[IndexEntry]]:
    """Parse one ``--index-info`` record.

    Accepted forms are ``MODE OID<TAB>PATH`` and ``MODE OID STAGE<TAB>PATH``.
    Stage 0 is the only supported stage because pygit's JSON index does not yet
    encode unmerged multi-stage entries. Mode 0 removes the named path.
    """
    metadata, sep, raw_path = record.partition("\t")
    if not sep:
        raise ValueError("--index-info input must contain a tab before the path")
    parts = metadata.split()
    if len(parts) not in {2, 3}:
        raise ValueError("--index-info input must be: <mode> <object> [stage]\\t<path>")

    mode, oid_text = parts[:2]
    if len(parts) == 3 and parts[2] != "0":
        raise ValueError("only index stage 0 is supported")
    path = _normalize_index_path(repo, raw_path)

    if mode == "0":
        return path, None
    return path, parse_cache_info(repo, mode, oid_text, path)


def update_index(
    repo: Repository,
    paths: Sequence[str] = (),
    *,
    add: bool = False,
    remove: bool = False,
    force_remove: bool = False,
    chmod: Optional[str] = None,
    cache_info: Sequence[Tuple[str, str, str]] = (),
    index_info: Sequence[str] = (),
) -> List[IndexEntry]:
    """Apply low-level index mutations atomically and return the final entries."""
    if chmod not in {None, "+x", "-x"}:
        raise ValueError("--chmod must be +x or -x")
    if force_remove and (add or remove or chmod is not None or cache_info or index_info):
        raise ValueError("--force-remove cannot be combined with other update modes")

    entries: Dict[str, IndexEntry] = dict(repo.index.entries)

    # Validate stdin/cache-info mutations first, then commit all at once.
    for mode, object_name, raw_path in cache_info:
        entry = parse_cache_info(repo, mode, object_name, raw_path)
        entries.pop(entry.path, None)
        _check_path_conflict(entries, entry.path)
        entries[entry.path] = entry

    for record in index_info:
        if record == "":
            continue
        path, entry = parse_index_info(repo, record)
        entries.pop(path, None)
        if entry is not None:
            _check_path_conflict(entries, path)
            entries[path] = entry

    normalized_paths = [_normalize_index_path(repo, path) for path in paths]
    for path in normalized_paths:
        target = repo.worktree / path
        tracked = path in entries

        if force_remove:
            if not tracked:
                raise KeyError(f"path is not in the index: {path}")
            entries.pop(path, None)
            continue

        if not _path_exists(target):
            if tracked and remove:
                entries.pop(path, None)
                continue
            if tracked:
                raise FileNotFoundError(f"{path}: needs removal (use --remove)")
            raise FileNotFoundError(path)

        if not tracked and not add:
            raise KeyError(f"{path}: is not in the index (use --add)")

        data, mode, size, mtime = _worktree_blob(target)
        if chmod is not None:
            if mode == "120000":
                raise ValueError(f"cannot change executable bit on symlink {path!r}")
            mode = "100755" if chmod == "+x" else "100644"

        oid = repo.store.write(BlobObject(data))
        entries.pop(path, None)
        _check_path_conflict(entries, path)
        entries[path] = IndexEntry(path, oid, mode, size, mtime)

    repo.index.entries = entries
    repo.index.save()
    return repo.index.all_entries()


def refresh_index(repo: Repository, paths: Sequence[str] = ()) -> List[str]:
    """Refresh stat metadata for unchanged tracked paths and return dirty paths."""
    selected: Set[str]
    if paths:
        selected = {_normalize_index_path(repo, path) for path in paths}
        missing = selected - set(repo.index.entries)
        if missing:
            raise KeyError(f"path is not in the index: {sorted(missing)[0]}")
    else:
        selected = set(repo.index.entries)

    dirty: List[str] = []
    for path in sorted(selected):
        entry = repo.index.entries[path]
        target = repo.worktree / path
        if not _path_exists(target):
            dirty.append(path)
            continue
        if entry.mode == "160000":
            dirty.append(path)
            continue

        data, mode, size, mtime = _worktree_blob(target)
        if _blob_oid(data) != entry.sha or mode != entry.mode:
            dirty.append(path)
            continue
        entry.size = size
        entry.mtime = mtime

    repo.index.save()
    return dirty


def _matches_path(path: str, patterns: Sequence[str]) -> bool:
    if not patterns:
        return True
    for pattern in patterns:
        pattern = pattern.strip("/")
        if any(ch in pattern for ch in "*?["):
            if fnmatch.fnmatchcase(path, pattern):
                return True
        elif path == pattern or path.startswith(pattern + "/"):
            return True
    return False


def _is_modified(repo: Repository, entry: IndexEntry) -> bool:
    target = repo.worktree / entry.path
    if not _path_exists(target) or entry.mode == "160000":
        return False
    try:
        data, mode, _, _ = _worktree_blob(target)
    except ValueError:
        return True
    return _blob_oid(data) != entry.sha or mode != entry.mode


def ls_files(
    repo: Repository,
    *,
    cached: bool = False,
    stage: bool = False,
    deleted: bool = False,
    modified: bool = False,
    patterns: Sequence[str] = (),
    error_unmatch: bool = False,
) -> List[str]:
    """Return formatted index records for the requested ``ls-files`` selectors."""
    if not any((cached, stage, deleted, modified)):
        cached = True

    matched_paths = [
        path for path in repo.index.paths() if _matches_path(path, patterns)
    ]
    if patterns and error_unmatch:
        for pattern in patterns:
            if not any(_matches_path(path, [pattern]) for path in repo.index.paths()):
                raise KeyError(f"pathspec {pattern!r} did not match any index entry")

    selected: Set[str] = set()
    if cached or stage:
        selected.update(matched_paths)
    if deleted:
        selected.update(
            path for path in matched_paths if not _path_exists(repo.worktree / path)
        )
    if modified:
        selected.update(
            path for path in matched_paths if _is_modified(repo, repo.index.entries[path])
        )

    lines: List[str] = []
    for path in sorted(selected):
        entry = repo.index.entries[path]
        if stage:
            lines.append(f"{entry.mode} {entry.sha} 0\t{path}")
        else:
            lines.append(path)
    return lines
