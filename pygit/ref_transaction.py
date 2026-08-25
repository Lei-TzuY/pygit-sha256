"""Transactional reference plumbing for ``update-ref`` and ``symbolic-ref``."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from .ref_query import check_ref_format
from .repo import Repository
from .refs import ZERO_SHA

_HEX = frozenset("0123456789abcdef")


@dataclass(frozen=True)
class RefUpdate:
    action: str
    refname: str
    new_oid: Optional[str] = None
    old_oid: Optional[str] = None


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
    path = repo.pygit_dir / "HEAD" if refname == "HEAD" else _ref_path(repo, refname)
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8").strip()


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
    while True:
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


def _resolve_new_oid(repo: Repository, value: str) -> str:
    if value == ZERO_SHA:
        return ZERO_SHA
    if _is_oid(value):
        oid = value.lower()
        if not repo.store.exists(oid):
            raise KeyError(f"Object not found: {oid}")
        return oid
    oid = repo.refs.resolve(value) or repo.store.resolve_prefix(value)
    if not oid or not repo.store.exists(oid):
        raise KeyError(f"Unknown object: {value!r}")
    return oid


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


def parse_update_records(records: Sequence[str]) -> List[RefUpdate]:
    """Parse a useful core of ``git update-ref --stdin`` transaction commands."""
    updates: List[RefUpdate] = []
    for record in records:
        if not record.strip():
            continue
        command, _, rest = record.partition(" ")
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


def update_refs(
    repo: Repository,
    updates: Sequence[RefUpdate],
    *,
    message: str = "update-ref",
    deref: bool = True,
) -> None:
    """Validate an entire ref transaction before changing any reference."""
    planned = []
    touched = set()

    for update in updates:
        _validate_refname(update.refname)
        physical, current = _resolve_direct(repo, update.refname, deref=deref)
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
                raise ValueError("zero object ID is not valid for update/create")
            planned.append((physical, current, new_oid))
        elif update.action == "delete":
            planned.append((physical, current, None))
        else:
            raise ValueError(f"unsupported ref action: {update.action!r}")

    # Prepare replacement files first, then publish with atomic os.replace.
    prepared = []
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
            prepared.append((refname, path, old_oid, new_oid, Path(tmp_name)))

        for refname, path, old_oid, new_oid, temp in prepared:
            if new_oid is None:
                if path.exists():
                    path.unlink()
            else:
                assert temp is not None
                os.replace(temp, path)
            repo.refs._append_reflog(refname, old_oid, new_oid, message)
            if refname.startswith("refs/heads/") and repo.refs.get_head() == f"ref: {refname}":
                repo.refs._append_reflog("HEAD", old_oid, new_oid, message)
    finally:
        for _, _, _, _, temp in prepared:
            if temp is not None and temp.exists():
                temp.unlink()


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
    old_oid = _resolve_direct(repo, name, deref=True)[1]
    path = repo.pygit_dir / "HEAD" if name == "HEAD" else _ref_path(repo, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"ref: {target}\n", encoding="utf-8")
    new_oid = _resolve_direct(repo, name, deref=True)[1]
    repo.refs._append_reflog(name, old_oid, new_oid, message, force=True)


def delete_symbolic_ref(repo: Repository, name: str, *, message: str = "symbolic-ref -d") -> None:
    _validate_refname(name)
    target = symbolic_target(repo, name)
    if target is None:
        raise RuntimeError(f"ref {name!r} is not a symbolic ref")
    old_oid = _resolve_direct(repo, name, deref=True)[1]
    path = repo.pygit_dir / "HEAD" if name == "HEAD" else _ref_path(repo, name)
    path.unlink()
    repo.refs._append_reflog(name, old_oid, None, message, force=True)
