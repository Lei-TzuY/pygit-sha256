"""Transactional reference updates and symbolic-reference helpers.

This module backs ``update-ref`` and ``symbolic-ref``.  Updates are validated
against a shadow ref state before anything is written, and multi-ref batches are
rolled back if a filesystem error occurs.  Object IDs are pygit's native
64-hex SHA-256 values.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .objects import CommitObject
from .ref_query import check_ref_format
from .repo import Repository
from .refs import ZERO_SHA


_HEX = frozenset("0123456789abcdef")
_MAX_SYMREF_DEPTH = 32


@dataclass(frozen=True)
class RefOperation:
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
        raise ValueError("reference name must be HEAD or begin with 'refs/'")
    return check_ref_format(refname)


def _ref_path(repo: Repository, refname: str) -> Path:
    _validate_refname(refname)
    if refname == "HEAD":
        return repo.pygit_dir / "HEAD"
    refs_root = (repo.pygit_dir / "refs").resolve()
    path = (repo.pygit_dir / refname).resolve()
    try:
        path.relative_to(refs_root)
    except ValueError as exc:
        raise ValueError(f"invalid reference name: {refname!r}") from exc
    return path


def read_ref_raw(repo: Repository, refname: str) -> Optional[str]:
    path = _ref_path(repo, refname)
    return path.read_text(encoding="utf-8").strip() if path.is_file() else None


def symbolic_target(repo: Repository, refname: str) -> Optional[str]:
    raw = read_ref_raw(repo, refname)
    if raw is None or not raw.startswith("ref:"):
        return None
    target = raw[4:].strip()
    _validate_refname(target, allow_head=False)
    return target


def dereference_ref(repo: Repository, refname: str) -> Tuple[str, Optional[str]]:
    """Return ``(terminal_refname, oid)`` while following symbolic refs."""
    current = _validate_refname(refname)
    seen = set()
    for _ in range(_MAX_SYMREF_DEPTH):
        if current in seen:
            raise RuntimeError(f"symbolic reference cycle at {current!r}")
        seen.add(current)
        raw = read_ref_raw(repo, current)
        if raw is None:
            return current, None
        if raw.startswith("ref:"):
            target = raw[4:].strip()
            _validate_refname(target, allow_head=False)
            current = target
            continue
        if not _is_oid(raw):
            raise RuntimeError(f"malformed ref {current}: expected symbolic ref or 64-hex OID")
        return current, raw.lower()
    raise RuntimeError("symbolic reference chain is too deep")


def resolve_ref_oid(repo: Repository, refname: str, *, deref: bool = True) -> Optional[str]:
    if deref:
        return dereference_ref(repo, refname)[1]
    raw = read_ref_raw(repo, refname)
    if raw is None:
        return None
    if raw.startswith("ref:"):
        return None
    if not _is_oid(raw):
        raise RuntimeError(f"malformed ref {refname}: expected a 64-hex OID")
    return raw.lower()


def _resolve_new_oid(repo: Repository, value: str) -> str:
    if value == ZERO_SHA:
        return ZERO_SHA
    candidate = repo.refs.resolve(value)
    if candidate and _is_oid(candidate) and repo.store.exists(candidate):
        return candidate.lower()
    candidate = repo.store.resolve_prefix(value)
    if candidate:
        return candidate.lower()
    if _is_oid(value) and repo.store.exists(value.lower()):
        return value.lower()
    raise KeyError(f"unknown object: {value!r}")


def _resolve_expected_oid(repo: Repository, value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if value == ZERO_SHA:
        return ZERO_SHA
    return _resolve_new_oid(repo, value)


def _validate_target_object(repo: Repository, refname: str, oid: str) -> None:
    if oid == ZERO_SHA:
        return
    obj = repo.store.read(oid)
    if refname.startswith("refs/heads/") and not isinstance(obj, CommitObject):
        raise ValueError(f"branch ref {refname!r} must point directly to a commit")


def _effective_ref(repo: Repository, refname: str, *, no_deref: bool) -> str:
    _validate_refname(refname)
    if no_deref:
        return refname
    terminal, _ = dereference_ref(repo, refname)
    return terminal


def _shadow_oid(repo: Repository, shadow: Dict[str, Optional[str]], refname: str) -> Optional[str]:
    if refname in shadow:
        return shadow[refname]
    value = resolve_ref_oid(repo, refname, deref=False)
    shadow[refname] = value
    return value


def _check_old(refname: str, current: Optional[str], expected: Optional[str]) -> None:
    if expected is None:
        return
    if expected == ZERO_SHA:
        if current is not None:
            raise RuntimeError(f"cannot lock ref {refname!r}: reference already exists")
        return
    if current != expected:
        actual = current or ZERO_SHA
        raise RuntimeError(
            f"cannot lock ref {refname!r}: expected {expected}, found {actual}"
        )


def _prune_empty_ref_parents(path: Path, stop: Path) -> None:
    current = path.parent
    stop = stop.resolve()
    while current.exists() and current.resolve() != stop:
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def apply_ref_operations(
    repo: Repository,
    operations: Sequence[RefOperation],
    *,
    no_deref: bool = False,
    message: str = "update-ref",
) -> None:
    """Validate and apply a batch of direct-ref operations atomically enough for pygit.

    All compare-and-swap predicates and target objects are checked against a
    shadow state before any file is changed. If an I/O error happens during the
    write phase, touched ref files are restored from snapshots.
    """
    if not operations:
        return

    shadow: Dict[str, Optional[str]] = {}
    normalized: List[RefOperation] = []

    for op in operations:
        if op.action not in {"update", "create", "delete", "verify"}:
            raise ValueError(f"unsupported ref transaction action: {op.action!r}")
        effective = _effective_ref(repo, op.refname, no_deref=no_deref)
        current = _shadow_oid(repo, shadow, effective)
        expected = _resolve_expected_oid(repo, op.old_oid)

        if op.action == "create":
            expected = ZERO_SHA if expected is None else expected
        _check_old(effective, current, expected)

        if op.action == "verify":
            normalized.append(RefOperation("verify", effective, None, expected))
            continue

        if op.action == "delete":
            shadow[effective] = None
            normalized.append(RefOperation("delete", effective, None, expected))
            continue

        if op.new_oid is None:
            raise ValueError(f"{op.action} requires a new object ID")
        new_oid = _resolve_new_oid(repo, op.new_oid)
        if new_oid == ZERO_SHA:
            raise ValueError("zero OID is only valid as an expected old value")
        _validate_target_object(repo, effective, new_oid)
        shadow[effective] = new_oid
        normalized.append(RefOperation(op.action, effective, new_oid, expected))

    touched = [op.refname for op in normalized if op.action != "verify"]
    snapshots: Dict[str, Optional[str]] = {
        name: read_ref_raw(repo, name) for name in dict.fromkeys(touched)
    }
    refs_root = repo.pygit_dir / "refs"

    try:
        for op in normalized:
            if op.action == "verify":
                continue
            path = _ref_path(repo, op.refname)
            old_oid = resolve_ref_oid(repo, op.refname, deref=False)
            if op.action == "delete":
                if path.exists():
                    path.unlink()
                    if op.refname.startswith("refs/"):
                        _prune_empty_ref_parents(path, refs_root)
                repo.refs._append_reflog(op.refname, old_oid, None, message)
                if op.refname != "HEAD" and repo.refs.current_branch() and (
                    op.refname == f"refs/heads/{repo.refs.current_branch()}"
                ):
                    repo.refs._append_reflog("HEAD", old_oid, None, message)
                continue

            assert op.new_oid is not None
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(op.new_oid, encoding="utf-8")
            repo.refs._append_reflog(op.refname, old_oid, op.new_oid, message)
            if op.refname != "HEAD" and repo.refs.current_branch() and (
                op.refname == f"refs/heads/{repo.refs.current_branch()}"
            ):
                repo.refs._append_reflog("HEAD", old_oid, op.new_oid, message)
    except OSError:
        for refname, raw in snapshots.items():
            path = _ref_path(repo, refname)
            if raw is None:
                if path.exists():
                    path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(raw, encoding="utf-8")
        raise


def parse_update_ref_records(records: Iterable[str]) -> List[RefOperation]:
    """Parse line-oriented ``update-ref --stdin`` transaction commands."""
    result: List[RefOperation] = []
    for record in records:
        record = record.strip()
        if not record:
            continue
        parts = record.split()
        action = parts[0]
        if action in {"update", "create"}:
            if len(parts) not in {3, 4}:
                raise ValueError(f"{action} record must be: {action} REF NEW [OLD]")
            result.append(
                RefOperation(action, parts[1], parts[2], parts[3] if len(parts) == 4 else None)
            )
        elif action in {"delete", "verify"}:
            if len(parts) not in {2, 3}:
                raise ValueError(f"{action} record must be: {action} REF [OLD]")
            result.append(
                RefOperation(action, parts[1], None, parts[2] if len(parts) == 3 else None)
            )
        else:
            raise ValueError(f"unsupported update-ref stdin command: {action!r}")
    return result


def query_symbolic_ref(repo: Repository, name: str) -> Optional[str]:
    _validate_refname(name)
    return symbolic_target(repo, name)


def set_symbolic_ref(
    repo: Repository,
    name: str,
    target: str,
    *,
    message: str = "symbolic-ref",
) -> None:
    _validate_refname(name)
    _validate_refname(target, allow_head=False)
    if name == target:
        raise ValueError("a symbolic ref cannot point to itself")

    # Reject cycles before writing. The target may legitimately be dangling.
    current = target
    seen = {name}
    for _ in range(_MAX_SYMREF_DEPTH):
        if current in seen:
            raise RuntimeError("symbolic reference update would create a cycle")
        seen.add(current)
        next_target = symbolic_target(repo, current)
        if next_target is None:
            break
        current = next_target
    else:
        raise RuntimeError("symbolic reference chain is too deep")

    path = _ref_path(repo, name)
    old_oid = resolve_ref_oid(repo, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"ref: {target}", encoding="utf-8")
    new_oid = resolve_ref_oid(repo, name)
    repo.refs._append_reflog(name, old_oid, new_oid, message, force=True)


def delete_symbolic_ref(repo: Repository, name: str, *, message: str = "symbolic-ref -d") -> None:
    if name == "HEAD":
        raise ValueError("refusing to delete HEAD")
    target = query_symbolic_ref(repo, name)
    if target is None:
        raise RuntimeError(f"ref {name!r} is not symbolic")
    old_oid = resolve_ref_oid(repo, name)
    path = _ref_path(repo, name)
    path.unlink()
    _prune_empty_ref_parents(path, repo.pygit_dir / "refs")
    repo.refs._append_reflog(name, old_oid, None, message, force=True)
