"""Transactional reference plumbing for ``update-ref`` and ``symbolic-ref``."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .objects import CommitObject
from .packed_refs import (
    packed_ref_value,
    packed_refs_path,
    remove_packed_refs,
)
from .ref_query import check_ref_format
from .repo import Repository
from .refs import ZERO_SHA

_HEX = frozenset("0123456789abcdef")
_MAX_SYMREF_DEPTH = 32
_TRANSACTION_CONTROLS = frozenset({"start", "prepare", "commit", "abort", "option"})


@dataclass(frozen=True)
class RefUpdate:
    action: str
    refname: str = ""
    new_oid: Optional[str] = None
    old_oid: Optional[str] = None
    deref: Optional[bool] = None


def _is_oid(value: str) -> bool:
    return len(value) == 64 and all(ch in _HEX for ch in value.lower())


def _validate_refname(refname: str, *, allow_head: bool = True) -> str:
    if allow_head and refname == "HEAD":
        return refname
    if not refname.startswith("refs/"):
        raise ValueError("reference name must begin with 'refs/'")
    return check_ref_format(refname)


def _ref_path(repo: Repository, refname: str) -> Path:
    _validate_refname(refname)
    path = (repo.pygit_dir / refname).resolve()
    try:
        path.relative_to(repo.pygit_dir.resolve())
    except ValueError as exc:
        raise ValueError(f"invalid reference name: {refname!r}") from exc
    return path


def _raw_value(repo: Repository, refname: str) -> Optional[str]:
    """Return a loose symbolic/direct value or the packed direct value."""
    path = repo.pygit_dir / "HEAD" if refname == "HEAD" else _ref_path(repo, refname)
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    if refname != "HEAD":
        return packed_ref_value(repo.pygit_dir, refname)
    return None


def symbolic_target(repo: Repository, refname: str) -> Optional[str]:
    value = _raw_value(repo, refname)
    if value and value.startswith("ref: "):
        target = value[5:].strip()
        _validate_refname(target, allow_head=False)
        return target
    return None


def _resolve_direct(repo: Repository, refname: str, *, deref: bool = True) -> Tuple[str, Optional[str]]:
    """Return ``(physical_refname, oid)`` after optional symbolic dereference."""
    _validate_refname(refname)
    current = refname
    seen = set()
    for _ in range(_MAX_SYMREF_DEPTH):
        if current in seen:
            raise RuntimeError(f"symbolic-ref cycle while resolving {refname!r}")
        seen.add(current)
        value = _raw_value(repo, current)
        if deref and value and value.startswith("ref: "):
            current = value[5:].strip()
            _validate_refname(current, allow_head=False)
            continue
        if value is None:
            return current, None
        if value.startswith("ref: "):
            return current, None
        if not _is_oid(value):
            raise RuntimeError(f"malformed ref {current}: expected a 64-hex object ID")
        return current, value.lower()
    raise RuntimeError(f"symbolic-ref chain is too deep while resolving {refname!r}")


def _resolve_new_oid(repo: Repository, value: str) -> str:
    if value == ZERO_SHA:
        return ZERO_SHA
    if _is_oid(value):
        oid = value.lower()
        if not repo.store.exists(oid):
            raise KeyError(f"Object not found: {oid}")
        return oid
    if value == "HEAD" or value.startswith("refs/"):
        _, oid = _resolve_direct(repo, value, deref=True)
        if oid and repo.store.exists(oid):
            return oid
    oid = repo.refs.resolve(value) or repo.store.resolve_prefix(value)
    if not oid or not _is_oid(oid) or not repo.store.exists(oid):
        raise KeyError(f"Unknown object: {value!r}")
    return oid.lower()


def _expected_old(repo: Repository, value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if value == ZERO_SHA:
        return ZERO_SHA
    return _resolve_new_oid(repo, value)


def _check_old(refname: str, current: Optional[str], expected: Optional[str]) -> None:
    if expected is None:
        return
    actual = current or ZERO_SHA
    if actual != expected:
        raise RuntimeError(
            f"cannot lock ref {refname!r}: expected {expected}, current value is {actual}"
        )


def _validate_target(repo: Repository, physical_refname: str, oid: str) -> None:
    obj = repo.store.read(oid)
    if physical_refname == "HEAD" or physical_refname.startswith("refs/heads/"):
        if not isinstance(obj, CommitObject):
            raise ValueError(
                f"trying to write non-commit object {oid} to branch-like ref {physical_refname!r}"
            )


def parse_update_records(records: Sequence[str]) -> List[RefUpdate]:
    """Parse ``update-ref --stdin`` direct-ref and transaction-control commands.

    Supported transaction controls are ``start``, ``prepare``, ``commit``,
    ``abort``, and the one-shot ``option no-deref`` modifier.  Symbolic-ref
    transaction commands and NUL framing intentionally remain separate work.
    """
    updates: List[RefUpdate] = []
    for record in records:
        if not record.strip():
            continue
        command, _, rest = record.partition(" ")

        if command in {"start", "prepare", "commit", "abort"}:
            if rest.strip():
                raise ValueError(f"{command} takes no arguments")
            updates.append(RefUpdate(command))
            continue

        if command == "option":
            if rest.strip() != "no-deref":
                raise ValueError(f"unsupported update-ref option: {rest.strip()!r}")
            updates.append(RefUpdate("option", "no-deref"))
            continue

        if command not in {"update", "create", "delete", "verify"}:
            raise ValueError(f"unsupported update-ref command: {command!r}")
        parts = rest.split()
        if command == "update" and len(parts) in {2, 3}:
            updates.append(RefUpdate(command, parts[0], parts[1], parts[2] if len(parts) == 3 else None))
        elif command == "create" and len(parts) == 2:
            updates.append(RefUpdate(command, parts[0], parts[1], ZERO_SHA))
        elif command == "delete" and len(parts) in {1, 2}:
            updates.append(RefUpdate(command, parts[0], None, parts[1] if len(parts) == 2 else None))
        elif command == "verify" and len(parts) in {1, 2}:
            updates.append(RefUpdate(command, parts[0], None, parts[1] if len(parts) == 2 else ZERO_SHA))
        else:
            raise ValueError(f"malformed update-ref record: {record!r}")
    return updates


def _snapshot_file(path: Path) -> Optional[bytes]:
    return path.read_bytes() if path.exists() else None


def _restore_file(path: Path, snapshot: Optional[bytes]) -> None:
    if snapshot is None:
        if path.exists():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(snapshot)


def _log_path(repo: Repository, refname: str) -> Path:
    return repo.pygit_dir / "logs" / ("HEAD" if refname == "HEAD" else refname)


def _plan_updates(
    repo: Repository,
    updates: Sequence[RefUpdate],
    *,
    deref: bool,
) -> List[Tuple[str, Optional[str], Optional[str]]]:
    """Resolve and validate one direct-ref transaction without publishing it."""
    planned: List[Tuple[str, Optional[str], Optional[str]]] = []
    touched = set()

    for update in updates:
        if update.action in _TRANSACTION_CONTROLS:
            raise ValueError(f"transaction control {update.action!r} is not valid inside a prepared batch")

        _validate_refname(update.refname)
        effective_deref = deref if update.deref is None else update.deref
        physical, current = _resolve_direct(repo, update.refname, deref=effective_deref)
        if physical in touched:
            raise ValueError(f"multiple updates for the same ref in one transaction: {physical}")
        touched.add(physical)
        expected = _expected_old(repo, update.old_oid)
        _check_old(update.refname, current, expected)

        if update.action == "verify":
            continue
        if update.action in {"update", "create"}:
            if update.new_oid is None:
                raise ValueError("update requires a new object ID")
            new_oid = _resolve_new_oid(repo, update.new_oid)
            if new_oid == ZERO_SHA:
                if update.action == "create":
                    raise ValueError("zero object ID is not valid for create")
                planned.append((physical, current, None))
                continue
            _validate_target(repo, physical, new_oid)
            planned.append((physical, current, new_oid))
        elif update.action == "delete":
            planned.append((physical, current, None))
        else:
            raise ValueError(f"unsupported ref action: {update.action!r}")

    return planned


def _apply_updates(
    repo: Repository,
    updates: Sequence[RefUpdate],
    *,
    message: str,
    deref: bool,
) -> None:
    planned = _plan_updates(repo, updates, deref=deref)

    packed_deletes = [
        refname
        for refname, _, new_oid in planned
        if new_oid is None
        and refname != "HEAD"
        and packed_ref_value(repo.pygit_dir, refname) is not None
    ]

    prepared = []
    snapshots: Dict[Path, Optional[bytes]] = {}
    try:
        for refname, old_oid, new_oid in planned:
            path = repo.pygit_dir / "HEAD" if refname == "HEAD" else _ref_path(repo, refname)
            if new_oid is None:
                prepared.append((refname, path, old_oid, None, None))
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent), text=True)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(new_oid + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            prepared.append((refname, path, old_oid, new_oid, Path(tmp_name)))

        for refname, path, _, _, _ in prepared:
            snapshots.setdefault(path, _snapshot_file(path))
            snapshots.setdefault(_log_path(repo, refname), _snapshot_file(_log_path(repo, refname)))
            if refname.startswith("refs/heads/") and repo.refs.get_head() == f"ref: {refname}":
                snapshots.setdefault(_log_path(repo, "HEAD"), _snapshot_file(_log_path(repo, "HEAD")))
        if packed_deletes:
            packed_path = packed_refs_path(repo.pygit_dir)
            snapshots.setdefault(packed_path, _snapshot_file(packed_path))

        # Publish all loose ref files first.
        for _, path, _, new_oid, temp in prepared:
            if new_oid is None:
                if path.exists():
                    path.unlink()
            else:
                assert temp is not None
                os.replace(temp, path)

        # Then remove packed backing values for deletions before reflogs are
        # emitted. If this rewrite fails the snapshot rollback restores both.
        if packed_deletes:
            remove_packed_refs(repo.pygit_dir, packed_deletes)

        for refname, _, old_oid, new_oid, _ in prepared:
            repo.refs._append_reflog(refname, old_oid, new_oid, message)
            if refname.startswith("refs/heads/") and repo.refs.get_head() == f"ref: {refname}":
                repo.refs._append_reflog("HEAD", old_oid, new_oid, message)
    except OSError:
        for path, snapshot in snapshots.items():
            _restore_file(path, snapshot)
        raise
    finally:
        for _, _, _, _, temp in prepared:
            if temp is not None and temp.exists():
                temp.unlink()


def update_refs(
    repo: Repository,
    updates: Sequence[RefUpdate],
    *,
    message: str = "update-ref",
    deref: bool = True,
) -> None:
    """Validate and publish one or more ref transactions.

    Ordinary ``RefUpdate`` sequences retain the original all-or-nothing batch
    behavior.  When transaction-control records are present, the sequence is
    interpreted as an ``update-ref --stdin`` session: ``start`` makes the
    current transaction explicit, ``prepare`` performs complete preflight,
    ``commit`` publishes it, and ``abort`` discards it.  An explicit or prepared
    transaction is automatically aborted at end-of-input, while an implicit
    transaction is committed at EOF.  ``option no-deref`` affects only the next
    ref-naming command.

    ``prepare`` intentionally performs validation rather than acquiring Git's
    cross-process ref backend lockfiles; commit revalidates immediately before
    publication.  The existing file replacement/snapshot rollback guarantees
    remain unchanged.
    """
    if not any(update.action in _TRANSACTION_CONTROLS for update in updates):
        _apply_updates(repo, updates, message=message, deref=deref)
        return

    pending: List[RefUpdate] = []
    explicit = False
    prepared = False
    next_no_deref = False

    for update in updates:
        action = update.action

        if action == "option":
            if update.refname != "no-deref":
                raise ValueError(f"unsupported update-ref option: {update.refname!r}")
            if prepared:
                raise RuntimeError("prepared transactions can only be closed")
            next_no_deref = True
            continue

        if action == "start":
            if prepared:
                raise RuntimeError("prepared transactions can only be closed")
            if explicit:
                raise RuntimeError("transaction already started")
            explicit = True
            continue

        if action == "prepare":
            if prepared:
                raise RuntimeError("transaction already prepared")
            _plan_updates(repo, pending, deref=deref)
            prepared = True
            explicit = True
            continue

        if action == "commit":
            _apply_updates(repo, pending, message=message, deref=deref)
            pending.clear()
            explicit = False
            prepared = False
            next_no_deref = False
            continue

        if action == "abort":
            pending.clear()
            explicit = False
            prepared = False
            next_no_deref = False
            continue

        if prepared:
            raise RuntimeError("prepared transactions can only be closed")

        effective_deref = False if next_no_deref else deref
        next_no_deref = False
        pending.append(
            RefUpdate(
                action=update.action,
                refname=update.refname,
                new_oid=update.new_oid,
                old_oid=update.old_oid,
                deref=effective_deref,
            )
        )

    if explicit or prepared:
        return
    if pending:
        _apply_updates(repo, pending, message=message, deref=deref)


def update_ref(
    repo: Repository,
    refname: str,
    new_oid: Optional[str],
    *,
    old_oid: Optional[str] = None,
    delete: bool = False,
    message: str = "update-ref",
    deref: bool = True,
) -> None:
    action = "delete" if delete else "update"
    update_refs(repo, [RefUpdate(action, refname, new_oid, old_oid)], message=message, deref=deref)


def set_symbolic_ref(repo: Repository, name: str, target: str, *, message: str = "symbolic-ref") -> None:
    _validate_refname(name)
    _validate_refname(target, allow_head=False)
    if name == target:
        raise ValueError("a symbolic ref cannot point to itself")

    current = target
    seen = {name}
    for _ in range(_MAX_SYMREF_DEPTH):
        if current in seen:
            raise RuntimeError("symbolic-ref update would create a cycle")
        seen.add(current)
        next_target = symbolic_target(repo, current)
        if next_target is None:
            break
        current = next_target
    else:
        raise RuntimeError("symbolic-ref chain is too deep")

    old_oid = _resolve_direct(repo, name, deref=True)[1]
    path = repo.pygit_dir / "HEAD" if name == "HEAD" else _ref_path(repo, name)
    log_path = _log_path(repo, name)
    packed_path = packed_refs_path(repo.pygit_dir)
    path_snapshot = _snapshot_file(path)
    log_snapshot = _snapshot_file(log_path)
    packed_snapshot = _snapshot_file(packed_path) if name != "HEAD" else None
    had_packed = name != "HEAD" and packed_ref_value(repo.pygit_dir, name) is not None

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"ref: {target}\n", encoding="utf-8")
        if had_packed:
            remove_packed_refs(repo.pygit_dir, [name])
        new_oid = _resolve_direct(repo, name, deref=True)[1]
        repo.refs._append_reflog(name, old_oid, new_oid, message, force=True)
    except OSError:
        _restore_file(path, path_snapshot)
        _restore_file(log_path, log_snapshot)
        if name != "HEAD":
            _restore_file(packed_path, packed_snapshot)
        raise


def delete_symbolic_ref(repo: Repository, name: str, *, message: str = "symbolic-ref -d") -> None:
    _validate_refname(name)
    if name == "HEAD":
        raise ValueError("deleting 'HEAD' is not allowed")
    target = symbolic_target(repo, name)
    if target is None:
        raise RuntimeError(f"ref {name!r} is not a symbolic ref")

    old_oid = _resolve_direct(repo, name, deref=True)[1]
    path = _ref_path(repo, name)
    log_path = _log_path(repo, name)
    packed_path = packed_refs_path(repo.pygit_dir)
    path_snapshot = _snapshot_file(path)
    log_snapshot = _snapshot_file(log_path)
    packed_snapshot = _snapshot_file(packed_path)

    try:
        path.unlink()
        remove_packed_refs(repo.pygit_dir, [name])
        repo.refs._append_reflog(name, old_oid, None, message, force=True)
    except OSError:
        _restore_file(path, path_snapshot)
        _restore_file(log_path, log_snapshot)
        _restore_file(packed_path, packed_snapshot)
        raise
