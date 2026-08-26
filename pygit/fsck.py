"""Repository integrity and connectivity checks for pygit's SHA-256 store.

The checker deliberately does not trust the normal happy-path object lookup.
It inventories loose and packed storage first, validates storage metadata, then
walks refs/index/shallow roots through the object graph while checking object
relationships and expected types. Callers may additionally include strict
reflog old/new object IDs as recovery roots.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from .objects import BlobObject, CommitObject, GitObject, TagObject, TreeObject
from .pack import PackReader
from .packed_refs import read_packed_refs
from .repo import Repository


_HEX = frozenset("0123456789abcdef")
_ZERO_OID = "0" * 64
_TREE_MODES = {"040000", "100644", "100755", "120000", "160000"}
_INDEX_MODES = {"100644", "100755", "120000", "160000"}
_TYPE_BY_MODE = {
    "040000": "tree",
    "100644": "blob",
    "100755": "blob",
    "120000": "blob",
    "160000": "commit",
}


@dataclass(frozen=True)
class FsckIssue:
    severity: str
    code: str
    message: str
    oid: Optional[str] = None
    source: Optional[str] = None

    def render(self) -> str:
        location = self.oid or self.source
        prefix = f"{self.severity}: {self.code}"
        if location:
            prefix += f" {location}"
        return f"{prefix}: {self.message}"


@dataclass
class FsckReport:
    issues: List[FsckIssue] = field(default_factory=list)
    checked_objects: Set[str] = field(default_factory=set)
    reachable: Set[str] = field(default_factory=set)
    unreachable: Set[str] = field(default_factory=set)
    dangling: Set[str] = field(default_factory=set)
    roots: Dict[str, str] = field(default_factory=dict)

    @property
    def errors(self) -> List[FsckIssue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def warnings(self) -> List[FsckIssue]:
        return [issue for issue in self.issues if issue.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors


def _is_oid(value: str) -> bool:
    return len(value) == 64 and all(char in _HEX for char in value.lower())


def _issue(
    report: FsckReport,
    severity: str,
    code: str,
    message: str,
    *,
    oid: Optional[str] = None,
    source: Optional[str] = None,
) -> None:
    report.issues.append(FsckIssue(severity, code, message, oid=oid, source=source))


def _loose_oids(repo: Repository, report: FsckReport) -> Set[str]:
    result: Set[str] = set()
    root = repo.store.root
    if not root.exists():
        return result
    for directory in sorted(root.iterdir(), key=lambda path: path.name):
        if directory.name in {"pack", "info"}:
            continue
        if not directory.is_dir() or len(directory.name) != 2 or not all(c in _HEX for c in directory.name.lower()):
            _issue(report, "warning", "invalid-object-path", "unexpected entry below objects/", source=str(directory.relative_to(repo.pygit_dir)))
            continue
        for path in sorted(directory.iterdir(), key=lambda item: item.name):
            oid = directory.name + path.name
            if not path.is_file() or len(path.name) != 62 or not _is_oid(oid):
                _issue(report, "warning", "invalid-object-path", "object filename is not a 64-hex SHA-256 path", source=str(path.relative_to(repo.pygit_dir)))
                continue
            result.add(oid.lower())
    return result


def _validate_pack_pair(idx_path: Path, report: FsckReport) -> Set[str]:
    source = str(idx_path)
    pack_path = idx_path.with_suffix(".pack")
    if not pack_path.exists():
        _issue(report, "error", "missing-pack", "index has no matching .pack file", source=source)
        return set()

    try:
        idx = idx_path.read_bytes()
        pack = pack_path.read_bytes()
    except OSError as exc:
        _issue(report, "error", "pack-read", str(exc), source=source)
        return set()

    if len(idx) < 1064 or idx[:4] != b"\xfftOc":
        _issue(report, "error", "bad-pack-index", "invalid index header or truncated index", source=source)
        return set()
    if struct.unpack(">I", idx[4:8])[0] != 2:
        _issue(report, "error", "bad-pack-index", "unsupported pack index version", source=source)
        return set()
    if hashlib.sha256(idx[:-32]).digest() != idx[-32:]:
        _issue(report, "error", "bad-pack-index-checksum", "SHA-256 index checksum mismatch", source=source)

    fanout = [struct.unpack(">I", idx[8 + i * 4 : 12 + i * 4])[0] for i in range(256)]
    if any(left > right for left, right in zip(fanout, fanout[1:])):
        _issue(report, "error", "bad-pack-fanout", "fanout table is not monotonic", source=source)
    count = fanout[-1]
    expected_size = 1064 + count * 72
    if len(idx) != expected_size:
        _issue(report, "error", "bad-pack-index-size", f"index size is {len(idx)}, expected {expected_size}", source=source)
        return set()

    pos = 1032
    shas: List[str] = []
    for _ in range(count):
        raw = idx[pos : pos + 64]
        pos += 64
        try:
            oid = raw.decode("ascii").lower()
        except UnicodeDecodeError:
            oid = ""
        if not _is_oid(oid):
            _issue(report, "error", "bad-pack-oid", "index contains an invalid object ID", source=source)
            continue
        shas.append(oid)
    if shas != sorted(shas) or len(shas) != len(set(shas)):
        _issue(report, "error", "bad-pack-order", "object IDs are not strictly sorted and unique", source=source)

    pos += count * 4  # CRC table
    offsets = [struct.unpack(">I", idx[pos + i * 4 : pos + (i + 1) * 4])[0] for i in range(count)]

    if len(pack) < 44 or pack[:4] != b"PACK":
        _issue(report, "error", "bad-pack", "invalid pack header or truncated pack", source=str(pack_path))
        return set(shas)
    version, pack_count = struct.unpack(">II", pack[4:12])
    if version != 2:
        _issue(report, "error", "bad-pack", f"unsupported pack version {version}", source=str(pack_path))
    if pack_count != count:
        _issue(report, "error", "pack-count", f"pack says {pack_count} objects but index says {count}", source=str(pack_path))
    if hashlib.sha256(pack[:-32]).digest() != pack[-32:]:
        _issue(report, "error", "bad-pack-checksum", "SHA-256 pack checksum mismatch", source=str(pack_path))
    for offset in offsets:
        if offset < 12 or offset >= max(12, len(pack) - 32):
            _issue(report, "error", "bad-pack-offset", f"object offset {offset} is outside the pack payload", source=source)
            break

    try:
        reader = PackReader(idx_path)
        for oid in shas:
            try:
                obj = reader.read_object(oid)
                if obj is None:
                    raise ValueError("object is listed but cannot be read")
                if obj.hash() != oid:
                    _issue(report, "error", "pack-object-hash", "reconstructed object hash does not match index ID", oid=oid, source=source)
            except Exception as exc:  # corrupt compressed streams and malformed payloads
                _issue(report, "error", "pack-object-read", str(exc), oid=oid, source=source)
    except Exception as exc:
        _issue(report, "error", "bad-pack-index", str(exc), source=source)

    return set(shas)


def _packed_oids(repo: Repository, report: FsckReport) -> Set[str]:
    pack_dir = repo.store.root / "pack"
    if not pack_dir.exists():
        return set()
    idx_paths = sorted(pack_dir.glob("*.idx"))
    indexed_packs = {path.with_suffix(".pack") for path in idx_paths}
    for pack_path in sorted(pack_dir.glob("*.pack")):
        if pack_path not in indexed_packs:
            _issue(report, "error", "missing-pack-index", "pack has no matching .idx file", source=str(pack_path))
    result: Set[str] = set()
    for idx_path in idx_paths:
        result.update(_validate_pack_pair(idx_path, report))
    return result


def _shallow_boundaries(repo: Repository, report: FsckReport) -> Set[str]:
    path = repo.pygit_dir / "shallow"
    if not path.exists():
        return set()
    result: Set[str] = set()
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        oid = raw.strip().lower()
        if not _is_oid(oid):
            _issue(report, "error", "bad-shallow", "expected a 64-hex commit ID", source=f"shallow:{lineno}")
            continue
        result.add(oid)
    return result


def _add_root(report: FsckReport, source: str, oid: Optional[str]) -> None:
    if oid is None:
        return
    value = oid.lower()
    if not _is_oid(value):
        _issue(report, "error", "bad-root", "expected a 64-hex object ID", source=source)
        return
    report.roots[source] = value


def _ref_roots(repo: Repository, report: FsckReport) -> None:
    try:
        _add_root(report, "HEAD", repo.refs.resolve_head())
    except Exception as exc:
        _issue(report, "error", "bad-head", str(exc), source="HEAD")

    packed: Mapping[str, object] = {}
    try:
        packed = read_packed_refs(repo.pygit_dir)
    except Exception as exc:
        _issue(report, "error", "bad-packed-refs", str(exc), source="packed-refs")

    names: Set[str] = set(packed)
    refs_root = repo.pygit_dir / "refs"
    if refs_root.exists():
        names.update(
            "refs/" + path.relative_to(refs_root).as_posix()
            for path in refs_root.rglob("*")
            if path.is_file()
        )
    for refname in sorted(names):
        try:
            _add_root(report, refname, repo.refs.resolve(refname))
        except Exception as exc:
            _issue(report, "error", "bad-ref", str(exc), source=refname)


def _index_roots(repo: Repository, report: FsckReport) -> None:
    for entry in repo.index.all_entries():
        source = f"index:{entry.path}"
        if not entry.path or entry.path.startswith(("/", "\\")) or "\x00" in entry.path or "\\" in entry.path:
            _issue(report, "error", "bad-index-path", "invalid repository path", source=source)
        else:
            parts = entry.path.split("/")
            if any(part in {"", ".", ".."} for part in parts):
                _issue(report, "error", "bad-index-path", "invalid repository path component", source=source)
        if entry.mode not in _INDEX_MODES:
            _issue(report, "error", "bad-index-mode", f"unsupported mode {entry.mode!r}", source=source)
        _add_root(report, source, entry.sha)


def _reflog_roots(repo: Repository, report: FsckReport) -> None:
    """Strictly add every non-zero reflog old/new OID as a recovery root."""
    # Local import avoids a module cycle: reflog expiry itself imports fsck for
    # current-ref reachability calculations. The shared parser gives fsck the
    # same safe-path and record validation rules as reflog show/expire.
    from .reflog_expire import _read_records, _target_logs

    try:
        logs = _target_logs(repo, (), all_refs=True)
    except Exception as exc:
        _issue(report, "error", "bad-reflog", str(exc), source="logs")
        return

    for ref, path in logs:
        try:
            records = _read_records(ref, path)
        except Exception as exc:
            _issue(report, "error", "bad-reflog", str(exc), source=ref)
            continue
        for index, record in enumerate(records, 1):
            for side, oid in (("old", record.old_oid), ("new", record.new_oid)):
                if oid == _ZERO_OID:
                    continue
                _add_root(report, f"reflog:{ref}:{index}:{side}", oid)


def _object_type(obj: GitObject) -> str:
    return obj.type_name.decode("ascii", "replace")


def _edge(
    report: FsckReport,
    edges: Dict[str, List[Tuple[str, str, str]]],
    source_oid: str,
    target_oid: str,
    expected: str,
    relation: str,
) -> None:
    if not _is_oid(target_oid):
        _issue(report, "error", "bad-object-id", f"{relation} is not a 64-hex object ID", oid=source_oid)
        return
    edges.setdefault(source_oid, []).append((target_oid.lower(), expected, relation))


def _validate_object(
    repo: Repository,
    report: FsckReport,
    oid: str,
    obj: GitObject,
    edges: Dict[str, List[Tuple[str, str, str]]],
    shallow: Set[str],
) -> None:
    try:
        reconstructed = obj.hash()
        if reconstructed != oid:
            _issue(report, "error", "noncanonical-object", f"re-serialized hash is {reconstructed}", oid=oid)
    except Exception as exc:
        _issue(report, "error", "malformed-object", str(exc), oid=oid)

    if isinstance(obj, BlobObject):
        return
    if isinstance(obj, CommitObject):
        _edge(report, edges, oid, getattr(obj, "tree", ""), "tree", "tree")
        if oid not in shallow:
            for parent in getattr(obj, "parents", []):
                _edge(report, edges, oid, parent, "commit", "parent")
        return
    if isinstance(obj, TagObject):
        expected = getattr(obj, "target_type", b"")
        if isinstance(expected, bytes):
            expected = expected.decode("ascii", "replace")
        if expected not in {"blob", "tree", "commit", "tag"}:
            _issue(report, "error", "bad-tag-type", f"unsupported target type {expected!r}", oid=oid)
            expected = "object"
        _edge(report, edges, oid, getattr(obj, "target_sha", ""), str(expected), "tag target")
        return
    if isinstance(obj, TreeObject):
        names: Set[str] = set()
        for entry in obj.entries:
            if entry.name in names:
                _issue(report, "error", "duplicate-tree-entry", f"duplicate name {entry.name!r}", oid=oid)
            names.add(entry.name)
            if not entry.name or entry.name in {".", ".."} or "/" in entry.name or "\x00" in entry.name:
                _issue(report, "error", "bad-tree-name", f"invalid entry name {entry.name!r}", oid=oid)
            if entry.mode not in _TREE_MODES:
                _issue(report, "error", "bad-tree-mode", f"unsupported mode {entry.mode!r} for {entry.name!r}", oid=oid)
                continue
            _edge(report, edges, oid, entry.sha, _TYPE_BY_MODE[entry.mode], f"tree entry {entry.name}")
        return
    _issue(report, "error", "unknown-object-type", _object_type(obj), oid=oid)


def _detect_cycles(report: FsckReport, edges: Mapping[str, Sequence[Tuple[str, str, str]]], objects: Mapping[str, GitObject]) -> None:
    state: Dict[str, int] = {}
    stack: List[str] = []
    reported: Set[Tuple[str, ...]] = set()

    def visit(oid: str) -> None:
        state[oid] = 1
        stack.append(oid)
        for target, _, _ in edges.get(oid, ()):
            if target not in objects:
                continue
            if state.get(target, 0) == 0:
                visit(target)
            elif state.get(target) == 1:
                start = stack.index(target)
                cycle = tuple(stack[start:] + [target])
                key = tuple(sorted(set(cycle)))
                if key not in reported:
                    reported.add(key)
                    _issue(report, "error", "object-cycle", " -> ".join(item[:12] for item in cycle), oid=target)
        stack.pop()
        state[oid] = 2

    for oid in sorted(objects):
        if state.get(oid, 0) == 0:
            visit(oid)


def fsck(
    repo: Repository,
    *,
    connectivity_only: bool = False,
    include_reflogs: bool = False,
) -> FsckReport:
    """Validate storage, roots, object links, types, cycles, and reachability.

    ``include_reflogs=True`` adds every non-zero old/new OID from every strict
    reflog below ``.pygit/logs`` as a recovery root. The Python API keeps this
    opt-in for backward compatibility; the installed ``pygit fsck`` command
    enables it by default, matching Git's CLI reachability model.
    """
    report = FsckReport()
    shallow = _shallow_boundaries(repo, report)
    _ref_roots(repo, report)
    _index_roots(repo, report)
    if include_reflogs:
        _reflog_roots(repo, report)
    for oid in sorted(shallow):
        _add_root(report, f"shallow:{oid}", oid)

    if connectivity_only:
        storage: Set[str] = set()
        queue = list(report.roots.values())
        seen: Set[str] = set()
        while queue:
            oid = queue.pop()
            if oid in seen:
                continue
            seen.add(oid)
            storage.add(oid)
            try:
                obj = repo.store.read(oid)
            except Exception:
                continue
            if isinstance(obj, CommitObject):
                queue.append(obj.tree)
                if oid not in shallow:
                    queue.extend(obj.parents)
            elif isinstance(obj, TreeObject):
                queue.extend(entry.sha for entry in obj.entries)
            elif isinstance(obj, TagObject):
                queue.append(obj.target_sha)
    else:
        storage = _loose_oids(repo, report) | _packed_oids(repo, report)

    objects: Dict[str, GitObject] = {}
    edges: Dict[str, List[Tuple[str, str, str]]] = {}
    for oid in sorted(storage | set(report.roots.values())):
        try:
            obj = repo.store.read(oid)
        except Exception as exc:
            _issue(report, "error", "object-read", str(exc), oid=oid)
            continue
        objects[oid] = obj
        report.checked_objects.add(oid)
        _validate_object(repo, report, oid, obj, edges, shallow)

    for source, outgoing in sorted(edges.items()):
        for target, expected, relation in outgoing:
            target_obj = objects.get(target)
            if target_obj is None:
                if target in storage or repo.store.exists(target):
                    try:
                        target_obj = repo.store.read(target)
                        objects[target] = target_obj
                        report.checked_objects.add(target)
                    except Exception as exc:
                        _issue(report, "error", "object-read", str(exc), oid=target)
                        continue
                else:
                    _issue(report, "error", "missing-object", f"{source[:12]} {relation} references a missing object", oid=target)
                    continue
            if expected != "object" and _object_type(target_obj) != expected:
                _issue(report, "error", "wrong-object-type", f"{source[:12]} {relation} expects {expected}, found {_object_type(target_obj)}", oid=target)

    _detect_cycles(report, edges, objects)

    queue = list(report.roots.values())
    while queue:
        oid = queue.pop()
        if oid in report.reachable:
            continue
        report.reachable.add(oid)
        for target, _, _ in edges.get(oid, ()):
            queue.append(target)

    all_known = set(storage) | set(objects)
    report.unreachable = all_known - report.reachable
    incoming_unreachable: Set[str] = set()
    for source, outgoing in edges.items():
        if source not in report.unreachable:
            continue
        incoming_unreachable.update(target for target, _, _ in outgoing if target in report.unreachable)
    report.dangling = report.unreachable - incoming_unreachable

    return report
