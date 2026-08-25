"""Read-only local reference inspection for ``pygit show-ref``.

The implementation composes the strict loose/packed reference helpers already
used by graph plumbing. Selection stays separate from output formatting so
callers can consume structured records without parsing CLI text.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

from .plumbing import list_refs, peel_oid, verify_ref
from .repo import Repository
from .revision import abbreviate_oid


@dataclass(frozen=True)
class ShowRefEntry:
    """One selected ref; synthetic peeled records have ``dereferenced=True``."""

    oid: str
    refname: str
    dereferenced: bool = False


def show_refs(
    repo: Repository,
    *,
    include_head: bool = False,
    branches: bool = False,
    tags: bool = False,
    patterns: Sequence[str] = (),
    verify_refs: Sequence[str] = (),
    dereference: bool = False,
) -> Tuple[ShowRefEntry, ...]:
    """Return refs using the useful core of Git ``show-ref`` semantics.

    Normal mode enumerates loose and packed refs, with loose refs shadowing
    packed entries. Patterns match complete path components from the end via
    the shared :func:`pygit.plumbing.list_refs` implementation.

    ``verify_refs`` switches to exact-ref mode. Every requested name must be a
    fully-qualified ``refs/...`` name and must exist. Annotated tag peeling is
    optional; lightweight tags produce no synthetic ``^{}`` entry.
    """

    if verify_refs and (patterns or include_head or branches or tags):
        raise ValueError("exact ref verification cannot be combined with ref filters")

    if verify_refs:
        pairs = [verify_ref(repo, refname) for refname in verify_refs]
    else:
        pairs = list_refs(
            repo,
            include_head=include_head,
            heads=branches,
            tags=tags,
            patterns=patterns,
        )

    result: List[ShowRefEntry] = []
    for oid, refname in pairs:
        result.append(ShowRefEntry(oid=oid, refname=refname))
        if not dereference or not refname.startswith("refs/tags/"):
            continue
        peeled = peel_oid(repo, oid)
        if peeled != oid:
            result.append(
                ShowRefEntry(
                    oid=peeled,
                    refname=f"{refname}^{{}}",
                    dereferenced=True,
                )
            )
    return tuple(result)


def _display_oid(
    repo: Repository,
    oid: str,
    *,
    hash_length: Optional[int],
    abbrev: Optional[int],
) -> str:
    if abbrev is not None:
        if abbrev < 1 or abbrev > 64:
            raise ValueError("show-ref abbreviation length must be between 1 and 64")
        minimum = max(4, abbrev)
        if repo.store.exists(oid):
            return abbreviate_oid(repo, oid, minimum=minimum)
        return oid[:minimum]

    if hash_length is not None:
        if hash_length < 1 or hash_length > 64:
            raise ValueError("show-ref hash length must be between 1 and 64")
        return oid[:hash_length]
    return oid


def format_show_refs(
    repo: Repository,
    entries: Iterable[ShowRefEntry],
    *,
    hash_only: bool = False,
    hash_length: Optional[int] = None,
    abbrev: Optional[int] = None,
) -> bytes:
    """Format selected refs as deterministic newline-terminated bytes.

    ``hash_length`` implies hash-only output. If ``abbrev`` is also active it
    controls displayed object-name length while hash mode still suppresses the
    refname. Existing objects use uniqueness-aware abbreviation; missing
    objects expose a deterministic recorded prefix instead of being resolved.
    """

    only_hash = hash_only or hash_length is not None
    lines: List[str] = []
    for entry in entries:
        shown = _display_oid(
            repo,
            entry.oid,
            hash_length=hash_length,
            abbrev=abbrev,
        )
        lines.append(shown if only_hash else f"{shown} {entry.refname}")
    return (("\n".join(lines) + "\n") if lines else "").encode("utf-8")
