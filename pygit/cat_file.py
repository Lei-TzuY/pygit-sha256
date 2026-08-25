"""Advanced ``cat-file`` plumbing for object inspection and batch queries.

Object-ish resolution is centralized in :mod:`pygit.revision` so cat-file and
rev-parse agree on refs, packed refs, abbreviated SHA-256 IDs, ancestry,
``REV:path`` walks, and ``^{type}`` peeling.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from .objects import GitObject
from .repo import Repository
from .revision import resolve_revision


@dataclass(frozen=True)
class CatFileRecord:
    expression: str
    oid: str
    type_name: str
    size: int
    content: bytes


def resolve_object(repo: Repository, expression: str) -> str:
    """Backward-compatible alias for the unified object-ish resolver."""
    return resolve_revision(repo, expression)


def inspect_object(repo: Repository, expression: str) -> CatFileRecord:
    oid = resolve_revision(repo, expression)
    obj: GitObject = repo.store.read(oid)
    content = obj.serialize()
    return CatFileRecord(
        expression=expression,
        oid=oid,
        type_name=obj.type_name.decode("ascii"),
        size=len(content),
        content=content,
    )


def object_exists(repo: Repository, expression: str) -> bool:
    try:
        inspect_object(repo, expression)
        return True
    except (KeyError, ValueError, RuntimeError):
        return False


def batch_records(repo: Repository, expressions: Iterable[str]) -> Iterable[Optional[CatFileRecord]]:
    """Inspect each input independently; missing/malformed names yield ``None``."""
    for raw in expressions:
        expression = raw.rstrip("\r\n")
        if not expression:
            yield None
            continue
        try:
            yield inspect_object(repo, expression)
        except (KeyError, ValueError, RuntimeError):
            yield None
