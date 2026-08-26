"""Packed reference backend and ``pack-refs`` implementation.

The on-disk format mirrors the useful core of Git's ``packed-refs`` file:

    # pack-refs with: peeled fully-peeled sorted
    <64-hex oid> <refname>
    ^<64-hex peeled-oid>        # optional, immediately after annotated tags

Loose refs always shadow packed refs.  Symbolic refs are never packed.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from fnmatch import fnmatchcase
import os
from pathlib import Path
import tempfile
from typing import Dict, Iterable, List, Optional, Sequence, Set, TYPE_CHECKING

from .objects import TagObject

if TYPE_CHECKING:
    from .repo import Repository


_HEX = frozenset("0123456789abcdef")


@dataclass(frozen=True)
class PackedRef:
    oid: str
    refname: str
    peeled_oid: Optional[str] = None


def _is_oid(value: str) -> bool:
    return len(value) == 64 and all(ch in _HEX for ch in value.lower())


def _validate_refname(refname: str) -> None:
    """Validate the safety-critical subset needed when parsing packed refs."""
    if not refname.startswith("refs/"):
        raise ValueError(f"packed ref must begin with 'refs/': {refname!r}")
    if refname.startswith("/") or refname.endswith("/") or "//" in refname:
        raise ValueError(f"invalid packed ref name: {refname!r}")
    if ".." in refname or "@{" in refname or refname.endswith("."):
        raise ValueError(f"invalid packed ref name: {refname!r}")
    forbidden = frozenset(" ~^:?*[\\")
    for component in refname.split("/"):
        if not component or component.startswith(".") or component.endswith(".lock"):
            raise ValueError(f"invalid packed ref name: {refname!r}")
    for char in refname:
        code = ord(char)
        if code < 0x20 or code == 0x7F or char in forbidden:
            raise ValueError(f"invalid packed ref name: {refname!r}")


def packed_refs_path(pygit_dir: Path) -> Path:
    return pygit_dir / "packed-refs"


def read_packed_refs(pygit_dir: Path) -> Dict[str, PackedRef]:
    """Parse ``.pygit/packed-refs`` strictly and return records by refname."""
    path = packed_refs_path(pygit_dir)
    if not path.exists():
        return {}

    records: Dict[str, PackedRef] = {}
    previous: Optional[str] = None
    for lineno, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            previous = None
            continue
        if line.startswith("^"):
            peeled = line[1:].lower()
            if previous is None:
                raise RuntimeError(f"malformed packed-refs line {lineno}: orphan peeled object")
            if not _is_oid(peeled):
                raise RuntimeError(f"malformed packed-refs line {lineno}: invalid peeled object ID")
            record = records[previous]
            if record.peeled_oid is not None:
                raise RuntimeError(f"malformed packed-refs line {lineno}: duplicate peeled object")
            records[previous] = replace(record, peeled_oid=peeled)
            previous = None
            continue

        oid, separator, refname = line.partition(" ")
        oid = oid.lower()
        if not separator or not refname or " " in refname or "\t" in refname:
            raise RuntimeError(f"malformed packed-refs line {lineno}: expected '<oid> <refname>'")
        if not _is_oid(oid):
            raise RuntimeError(f"malformed packed-refs line {lineno}: invalid object ID")
        try:
            _validate_refname(refname)
        except ValueError as exc:
            raise RuntimeError(f"malformed packed-refs line {lineno}: {exc}") from exc
        if refname in records:
            raise RuntimeError(f"malformed packed-refs line {lineno}: duplicate ref {refname}")
        records[refname] = PackedRef(oid, refname)
        previous = refname

    return records


def packed_ref_value(pygit_dir: Path, refname: str) -> Optional[str]:
    record = read_packed_refs(pygit_dir).get(refname)
    return record.oid if record else None


def list_packed_refnames(pygit_dir: Path, prefix: str = "refs/") -> List[str]:
    return sorted(name for name in read_packed_refs(pygit_dir) if name.startswith(prefix))


def _render(records: Iterable[PackedRef]) -> str:
    lines = ["# pack-refs with: peeled fully-peeled sorted"]
    for record in sorted(records, key=lambda item: item.refname):
        lines.append(f"{record.oid} {record.refname}")
        if record.peeled_oid is not None:
            lines.append(f"^{record.peeled_oid}")
    return "\n".join(lines) + "\n"


def _atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".packed-refs.", dir=str(path.parent), text=True)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def write_packed_refs(pygit_dir: Path, records: Iterable[PackedRef]) -> None:
    """Atomically replace the packed-ref file with *records*."""
    path = packed_refs_path(pygit_dir)
    records = list(records)
    if not records:
        if path.exists():
            path.unlink()
        return
    _atomic_write(path, _render(records))


def remove_packed_refs(pygit_dir: Path, refnames: Sequence[str]) -> Set[str]:
    """Remove *refnames* from packed storage and return those that existed."""
    wanted = set(refnames)
    if not wanted:
        return set()
    records = read_packed_refs(pygit_dir)
    removed = wanted.intersection(records)
    if not removed:
        return set()
    write_packed_refs(
        pygit_dir,
        (record for name, record in records.items() if name not in removed),
    )
    return removed


def _peel(repo: "Repository", oid: str) -> str:
    current = oid
    seen: Set[str] = set()
    while True:
        if current in seen:
            raise RuntimeError(f"tag cycle while peeling {oid}")
        seen.add(current)
        obj = repo.store.read(current)
        if not isinstance(obj, TagObject):
            return current
        current = obj.target_sha


def _loose_direct_refs(repo: "Repository") -> Dict[str, str]:
    root = repo.pygit_dir / "refs"
    result: Dict[str, str] = {}
    if not root.exists():
        return result

    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        refname = "refs/" + path.relative_to(root).as_posix()
        raw = path.read_text(encoding="utf-8").strip()
        if raw.startswith("ref: "):
            continue
        if not _is_oid(raw):
            raise RuntimeError(f"malformed loose ref {refname}: expected a 64-hex object ID")
        result[refname] = raw.lower()
    return result


def _matches_any(refname: str, patterns: Sequence[str]) -> bool:
    """Return whether *refname* matches any Git-style pack-refs glob pattern.

    ``fnmatchcase`` intentionally keeps matching case-sensitive and lets ``*``
    span ``/``, matching the observable behavior of native ``pack-refs`` ref
    patterns for full refnames.
    """
    return any(fnmatchcase(refname, pattern) for pattern in patterns)


def _selected_for_packing(
    refname: str,
    *,
    all_refs: bool,
    includes: Sequence[str],
    excludes: Sequence[str],
) -> bool:
    if all_refs:
        selected = True
    elif includes:
        selected = _matches_any(refname, includes)
    else:
        selected = refname.startswith("refs/tags/")
    return selected and not _matches_any(refname, excludes)


def _restore_bytes(path: Path, data: Optional[bytes]) -> None:
    if data is None:
        if path.exists():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def pack_refs(
    repo: "Repository",
    *,
    all_refs: bool = False,
    prune: bool = True,
    includes: Sequence[str] = (),
    excludes: Sequence[str] = (),
) -> List[PackedRef]:
    """Pack loose refs and optionally prune their loose files.

    By default only tags are newly packed, matching Git's conservative default.
    ``includes`` replaces that default tag selection when ``all_refs`` is false;
    repeated include patterns form a union. ``all_refs=True`` selects every
    direct ref regardless of include patterns. Exclude patterns are applied last
    in every mode and therefore win over tags, includes, and ``all_refs``.

    Existing packed refs are always preserved unless shadowed by a newly packed
    loose ref. Excluded loose refs remain in place, so an older packed backing
    value stays safely shadowed rather than being refreshed or exposed.
    """
    existing = read_packed_refs(repo.pygit_dir)
    loose = _loose_direct_refs(repo)
    next_records = dict(existing)
    selected: List[str] = []

    for refname, oid in loose.items():
        if not _selected_for_packing(
            refname,
            all_refs=all_refs,
            includes=includes,
            excludes=excludes,
        ):
            continue
        if not repo.store.exists(oid):
            raise KeyError(f"Object not found for {refname}: {oid}")
        obj = repo.store.read(oid)
        peeled = _peel(repo, oid) if isinstance(obj, TagObject) else None
        next_records[refname] = PackedRef(oid, refname, peeled)
        selected.append(refname)

    packed_path = packed_refs_path(repo.pygit_dir)
    packed_snapshot = packed_path.read_bytes() if packed_path.exists() else None
    loose_snapshots: Dict[Path, bytes] = {}
    refs_root = repo.pygit_dir / "refs"
    for refname in selected:
        path = refs_root / refname[len("refs/") :]
        if path.exists():
            loose_snapshots[path] = path.read_bytes()

    try:
        write_packed_refs(repo.pygit_dir, next_records.values())
        if prune:
            for path in loose_snapshots:
                path.unlink()
            for path in sorted(
                {path.parent for path in loose_snapshots},
                key=lambda p: len(p.parts),
                reverse=True,
            ):
                current = path
                while current != refs_root and current.exists():
                    try:
                        current.rmdir()
                    except OSError:
                        break
                    current = current.parent
    except OSError:
        _restore_bytes(packed_path, packed_snapshot)
        for path, data in loose_snapshots.items():
            _restore_bytes(path, data)
        raise

    return [next_records[name] for name in sorted(selected)]
