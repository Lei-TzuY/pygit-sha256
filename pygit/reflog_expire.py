"""Reachability-aware reflog expiry with atomic rewrites.

Phase 72 complements :mod:`pygit.prune`: reflog records remain recovery roots
until an explicit expiry policy removes them.  The implementation fails closed
on unhealthy current connectivity or malformed target logs and never changes
refs or objects directly.
"""

from __future__ import annotations

import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .fsck import fsck
from .repo import Repository


_HEX = frozenset("0123456789abcdef")
_ZERO_OID = "0" * 64
_TZ = re.compile(r"^[+-][0-9]{4}$")
_DEFAULT_EXPIRE_SECONDS = 90 * 24 * 60 * 60
_DEFAULT_UNREACHABLE_SECONDS = 30 * 24 * 60 * 60


@dataclass(frozen=True)
class ReflogExpireEntry:
    """One reflog record selected for expiry."""

    ref: str
    old_oid: str
    new_oid: str
    timestamp: int
    message: str
    reason: str


@dataclass(frozen=True)
class ReflogExpireResult:
    """Summary of one reflog-expire pass."""

    scanned_logs: int
    scanned_entries: int
    expired: int
    kept: int
    rewritten_logs: Tuple[str, ...]
    entries: Tuple[ReflogExpireEntry, ...]
    expire_before: float
    expire_unreachable_before: float
    dry_run: bool


@dataclass(frozen=True)
class _ParsedRecord:
    ref: str
    old_oid: str
    new_oid: str
    timestamp: int
    message: str
    raw: str


def default_reflog_expire_before(now: Optional[float] = None) -> float:
    current = time.time() if now is None else float(now)
    return current - _DEFAULT_EXPIRE_SECONDS


def default_reflog_unreachable_before(now: Optional[float] = None) -> float:
    current = time.time() if now is None else float(now)
    return current - _DEFAULT_UNREACHABLE_SECONDS


def _is_oid(value: str) -> bool:
    return len(value) == 64 and all(char in _HEX for char in value.lower())


def _safe_log_path(repo: Repository, ref: str) -> Path:
    if ref == "HEAD":
        relative = Path("HEAD")
    elif ref.startswith("refs/"):
        parts = ref.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError(f"invalid reflog ref name: {ref!r}")
        relative = Path(*parts)
    else:
        raise ValueError("reflog refs must be HEAD or fully-qualified refs/... names")

    root = repo.pygit_dir / "logs"
    path = root / relative
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"reflog path escapes logs/: {ref!r}") from exc
    if path.is_symlink():
        raise RuntimeError(f"refusing symbolic-link reflog: {ref}")
    return path


def _target_logs(
    repo: Repository,
    refs: Sequence[str],
    *,
    all_refs: bool,
) -> List[Tuple[str, Path]]:
    root = repo.pygit_dir / "logs"
    selected: Dict[Path, str] = {}

    if all_refs and root.exists():
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise RuntimeError(
                    f"refusing symbolic-link entry below logs/: {path.relative_to(root)}"
                )
            if not path.is_file():
                continue
            try:
                resolved = path.resolve()
                resolved.relative_to(root.resolve())
            except ValueError as exc:
                raise RuntimeError("reflog entry escapes logs/") from exc
            relative = path.relative_to(root).as_posix()
            ref = "HEAD" if relative == "HEAD" else relative
            selected[path] = ref

    requested = list(refs)
    if not all_refs and not requested:
        requested = ["HEAD"]
    for ref in requested:
        path = _safe_log_path(repo, ref)
        if not path.exists():
            raise FileNotFoundError(f"reflog does not exist: {ref}")
        if not path.is_file():
            raise RuntimeError(f"reflog is not a regular file: {ref}")
        selected[path] = ref

    return sorted(((ref, path) for path, ref in selected.items()), key=lambda item: item[0])


def _parse_record(ref: str, path: Path, lineno: int, raw: str) -> _ParsedRecord:
    line = raw[:-1] if raw.endswith("\n") else raw
    if line.endswith("\r"):
        line = line[:-1]
    metadata, separator, message = line.partition("\t")
    parts = metadata.split()
    location = f"{path}:{lineno}"
    if not separator or len(parts) < 4:
        raise ValueError(f"malformed reflog record {location}")

    old_oid = parts[0].lower()
    new_oid = parts[1].lower()
    if not _is_oid(old_oid) or not _is_oid(new_oid):
        raise ValueError(f"malformed reflog object ID at {location}")
    try:
        timestamp = int(parts[-2])
    except ValueError as exc:
        raise ValueError(f"malformed reflog timestamp at {location}") from exc
    if timestamp < 0:
        raise ValueError(f"negative reflog timestamp at {location}")
    if not _TZ.fullmatch(parts[-1]):
        raise ValueError(f"malformed reflog timezone at {location}")

    canonical_raw = line + "\n"
    return _ParsedRecord(
        ref=ref,
        old_oid=old_oid,
        new_oid=new_oid,
        timestamp=timestamp,
        message=message,
        raw=canonical_raw,
    )


def _read_records(ref: str, path: Path) -> List[_ParsedRecord]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise ValueError(f"reflog is not valid UTF-8: {ref}") from exc
    return [
        _parse_record(ref, path, lineno, raw)
        for lineno, raw in enumerate(text.splitlines(keepends=True), 1)
    ]


def _current_reachable(repo: Repository) -> Set[str]:
    report = fsck(repo, connectivity_only=True)
    if report.errors:
        raise RuntimeError(
            "cannot expire reflogs for an unhealthy repository: "
            + report.errors[0].render()
        )
    return set(report.reachable)


def _entry_is_unreachable(record: _ParsedRecord, reachable: Set[str]) -> bool:
    historical = [
        oid
        for oid in (record.old_oid, record.new_oid)
        if oid != _ZERO_OID
    ]
    return bool(historical) and all(oid not in reachable for oid in historical)


def _write_temp(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="wb",
        dir=str(path.parent),
        prefix=f".{path.name}.expire-",
        delete=False,
    )
    temp_path = Path(handle.name)
    try:
        with handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        return temp_path
    except Exception:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise


def _commit_rewrites(plans: Dict[Path, bytes]) -> None:
    """Prepare every replacement before changing the first reflog."""
    originals = {path: path.read_bytes() for path in plans}
    prepared: Dict[Path, Path] = {}
    replaced: List[Path] = []
    try:
        for path, data in plans.items():
            prepared[path] = _write_temp(path, data)
        for path in sorted(plans, key=lambda item: str(item)):
            os.replace(str(prepared[path]), str(path))
            prepared.pop(path, None)
            replaced.append(path)
    except OSError:
        for temp_path in prepared.values():
            try:
                temp_path.unlink()
            except OSError:
                pass
        # Best-effort rollback uses the same atomic replacement primitive.
        for path in reversed(replaced):
            rollback = _write_temp(path, originals[path])
            os.replace(str(rollback), str(path))
        raise


def expire_reflogs(
    repo: Repository,
    refs: Sequence[str] = (),
    *,
    all_refs: bool = False,
    expire_before: Optional[float] = None,
    expire_unreachable_before: Optional[float] = None,
    dry_run: bool = False,
) -> ReflogExpireResult:
    """Expire old reflog records without modifying refs or objects.

    General expiry removes records at or before ``expire_before`` regardless of
    reachability.  The unreachable cutoff applies only when every non-zero OID
    in a record is outside the current refs/index/shallow closure.  All target
    logs are parsed and all rewrite contents are prepared before the first
    replacement.
    """

    now = time.time()
    general_cutoff = (
        default_reflog_expire_before(now)
        if expire_before is None
        else float(expire_before)
    )
    unreachable_cutoff = (
        default_reflog_unreachable_before(now)
        if expire_unreachable_before is None
        else float(expire_unreachable_before)
    )

    reachable = _current_reachable(repo)
    targets = _target_logs(repo, refs, all_refs=all_refs)

    parsed: Dict[Path, List[_ParsedRecord]] = {}
    names: Dict[Path, str] = {}
    for ref, path in targets:
        names[path] = ref
        parsed[path] = _read_records(ref, path)

    expired_entries: List[ReflogExpireEntry] = []
    plans: Dict[Path, bytes] = {}
    scanned_entries = 0
    kept = 0

    for path, records in parsed.items():
        scanned_entries += len(records)
        survivors: List[str] = []
        changed = False
        for record in records:
            reason: Optional[str] = None
            if record.timestamp <= general_cutoff:
                reason = "expire"
            elif (
                record.timestamp <= unreachable_cutoff
                and _entry_is_unreachable(record, reachable)
            ):
                reason = "expire-unreachable"

            if reason is None:
                survivors.append(record.raw)
                kept += 1
                continue

            changed = True
            expired_entries.append(
                ReflogExpireEntry(
                    ref=record.ref,
                    old_oid=record.old_oid,
                    new_oid=record.new_oid,
                    timestamp=record.timestamp,
                    message=record.message,
                    reason=reason,
                )
            )

        if changed:
            plans[path] = "".join(survivors).encode("utf-8")

    if not dry_run and plans:
        _commit_rewrites(plans)

    rewritten = tuple(sorted(names[path] for path in plans))
    return ReflogExpireResult(
        scanned_logs=len(targets),
        scanned_entries=scanned_entries,
        expired=len(expired_entries),
        kept=kept,
        rewritten_logs=rewritten,
        entries=tuple(expired_entries),
        expire_before=general_cutoff,
        expire_unreachable_before=unreachable_cutoff,
        dry_run=dry_run,
    )
