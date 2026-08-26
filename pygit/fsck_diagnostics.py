"""Supplemental ``fsck`` graph diagnostics such as ``--root`` and ``--tags``.

These reports intentionally inspect the complete object set already validated by
:func:`pygit.fsck.fsck`.  They do not participate in reachability decisions and
therefore remain independent of explicit fsck heads, dangling suppression, or
lost-found recovery.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Set, Tuple

from .fsck import FsckReport
from .objects import CommitObject, TagObject
from .repo import Repository


_HEX = frozenset("0123456789abcdef")


@dataclass(frozen=True)
class FsckTagDiagnostic:
    """One annotated tag relationship suitable for native-style rendering."""

    tag_oid: str
    target_oid: str
    target_type: str
    tag_name: str


def _is_oid(value: str) -> bool:
    return len(value) == 64 and all(char in _HEX for char in value.lower())


def _shallow_boundaries(repo: Repository) -> Set[str]:
    path = repo.pygit_dir / "shallow"
    if not path.exists():
        return set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()
    return {
        line.strip().lower()
        for line in lines
        if _is_oid(line.strip())
    }


def root_commits(repo: Repository, report: FsckReport) -> Tuple[str, ...]:
    """Return validated commit objects that are roots of the fsck commit graph.

    The result is based on every object checked by a full fsck, not merely the
    reachable set.  A commit recorded in ``shallow`` is treated as a synthetic
    root because fsck deliberately suppresses its stored parent edges.
    """

    shallow = _shallow_boundaries(repo)
    roots: List[str] = []
    for oid in sorted(report.checked_objects):
        try:
            obj = repo.store.read(oid)
        except Exception:
            continue
        if not isinstance(obj, CommitObject):
            continue
        if oid in shallow or not getattr(obj, "parents", ()):
            roots.append(oid)
    return tuple(roots)


def annotated_tags(repo: Repository, report: FsckReport) -> Tuple[FsckTagDiagnostic, ...]:
    """Return validated annotated tag objects from the complete checked set."""

    result: List[FsckTagDiagnostic] = []
    for oid in sorted(report.checked_objects):
        try:
            obj = repo.store.read(oid)
        except Exception:
            continue
        if not isinstance(obj, TagObject):
            continue
        target_type = getattr(obj, "target_type", b"")
        if isinstance(target_type, bytes):
            target_type = target_type.decode("ascii", "replace")
        target_oid = str(getattr(obj, "target_sha", "")).lower()
        result.append(
            FsckTagDiagnostic(
                tag_oid=oid,
                target_oid=target_oid,
                target_type=str(target_type),
                tag_name=str(getattr(obj, "tag_name", "")),
            )
        )
    return tuple(result)


def format_tag_diagnostic(entry: FsckTagDiagnostic) -> str:
    """Render the same compact relationship form used by native ``git fsck``."""

    return (
        f"tagged {entry.target_type} {entry.target_oid} "
        f"({entry.tag_name}) in {entry.tag_oid}"
    )
