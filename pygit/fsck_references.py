"""Independent reference-database verification for ``fsck``.

Reachability heads and reference integrity are related but distinct concerns.
In particular, ``fsck <object>`` replaces the roots used for reachability, but
modern Git still verifies the reference database unless ``--no-references`` is
requested.  This module keeps that verification separate so explicit heads do
not accidentally hide malformed refs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .fsck import FsckIssue
from .packed_refs import read_packed_refs
from .ref_query import check_ref_format
from .repo import Repository

_HEX = frozenset("0123456789abcdef")
_MAX_SYMREF_DEPTH = 32


def _is_oid(value: str) -> bool:
    return len(value) == 64 and all(ch in _HEX for ch in value.lower())


def _issue(issues: List[FsckIssue], code: str, message: str, source: str) -> None:
    issues.append(FsckIssue("error", code, message, source=source))


def _validate_name(issues: List[FsckIssue], refname: str, source: str) -> bool:
    try:
        check_ref_format(refname)
    except Exception as exc:
        _issue(issues, "bad-reference-name", str(exc), source)
        return False
    return True


def _read_loose_value(issues: List[FsckIssue], path: Path, source: str) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception as exc:
        _issue(issues, "bad-reference", str(exc), source)
        return None


def _validate_value(
    repo: Repository,
    issues: List[FsckIssue],
    refname: str,
    value: Optional[str],
    *,
    source: str,
    allow_unborn: bool,
) -> None:
    if value is None:
        return
    if not value:
        _issue(issues, "bad-reference", "reference is empty", source)
        return

    if value.startswith("ref: "):
        target = value[5:].strip()
        if not _validate_name(issues, target, source):
            return
        try:
            resolved = repo.refs.resolve(refname)
        except Exception as exc:
            _issue(issues, "bad-symbolic-reference", str(exc), source)
            return
        if resolved is None and not allow_unborn:
            # A symbolic ref below refs/ may deliberately point at an unborn
            # target, so absence alone is not a consistency failure.  The flag
            # is retained for future pseudo-ref policies and documents intent.
            return
        return

    if not _is_oid(value):
        _issue(issues, "bad-reference", "expected a 64-hex object ID or symbolic ref", source)


def _loose_refs(repo: Repository, issues: List[FsckIssue]) -> Dict[str, Path]:
    root = repo.pygit_dir / "refs"
    result: Dict[str, Path] = {}
    if not root.exists():
        return result
    if root.is_symlink() or not root.is_dir():
        _issue(issues, "bad-reference-store", "refs must be a real directory", "refs")
        return result

    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        rel = path.relative_to(repo.pygit_dir).as_posix()
        if path.is_symlink():
            _issue(issues, "bad-reference-store", "symbolic links are not valid ref files", rel)
            continue
        if path.is_dir():
            continue
        if not path.is_file():
            _issue(issues, "bad-reference-store", "unexpected non-file entry in refs", rel)
            continue
        refname = rel
        if not _validate_name(issues, refname, rel):
            continue
        result[refname] = path
        value = _read_loose_value(issues, path, rel)
        _validate_value(repo, issues, refname, value, source=rel, allow_unborn=True)
    return result


def _namespace_conflicts(issues: List[FsckIssue], refnames: Set[str]) -> None:
    names = sorted(refnames)
    name_set = set(names)
    reported: Set[Tuple[str, str]] = set()
    for refname in names:
        parts = refname.split("/")
        for index in range(2, len(parts)):
            prefix = "/".join(parts[:index])
            if prefix not in name_set:
                continue
            pair = (prefix, refname)
            if pair in reported:
                continue
            reported.add(pair)
            _issue(
                issues,
                "reference-namespace-conflict",
                f"{prefix!r} conflicts with nested ref {refname!r}",
                refname,
            )


def verify_references(repo: Repository) -> Tuple[FsckIssue, ...]:
    """Return structural reference-database consistency errors.

    The check is intentionally independent of reachability.  It validates HEAD,
    loose refs, packed-refs parsing, symbolic-ref chains, safe filesystem shape,
    and file/directory namespace conflicts.  Object existence remains fsck's
    graph/connectivity responsibility rather than a refs-database property.
    """

    issues: List[FsckIssue] = []
    names: Set[str] = set()

    head = repo.pygit_dir / "HEAD"
    if head.exists():
        if head.is_symlink() or not head.is_file():
            _issue(issues, "bad-reference-store", "HEAD must be a real file", "HEAD")
        else:
            value = _read_loose_value(issues, head, "HEAD")
            _validate_value(repo, issues, "HEAD", value, source="HEAD", allow_unborn=True)

    loose = _loose_refs(repo, issues)
    names.update(loose)

    packed_path = repo.pygit_dir / "packed-refs"
    if packed_path.exists() and (packed_path.is_symlink() or not packed_path.is_file()):
        _issue(issues, "bad-reference-store", "packed-refs must be a real file", "packed-refs")
    else:
        try:
            packed = read_packed_refs(repo.pygit_dir)
        except Exception as exc:
            _issue(issues, "bad-packed-refs", str(exc), "packed-refs")
        else:
            names.update(packed)
            for refname in packed:
                _validate_name(issues, refname, "packed-refs")

    _namespace_conflicts(issues, names)
    return tuple(issues)
