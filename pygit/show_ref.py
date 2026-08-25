"""Read-only local reference inspection for ``pygit show-ref``.

The implementation composes the strict loose/packed reference helpers already
used by graph plumbing. Selection stays separate from output formatting so
callers can consume structured records without parsing CLI text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Set, Tuple

from .packed_refs import read_packed_refs
from .plumbing import list_refs, peel_oid, verify_ref
from .ref_query import check_ref_format
from .repo import Repository
from .revision import abbreviate_oid


@dataclass(frozen=True)
class ShowRefEntry:
    """One selected ref; synthetic peeled records have ``dereferenced=True``."""

    oid: str
    refname: str
    dereferenced: bool = False


@dataclass(frozen=True)
class ExcludeExistingResult:
    """Filtered stdin bytes plus non-fatal malformed-input warnings."""

    output: bytes
    warnings: Tuple[str, ...]


def _stored_refnames(repo: Repository) -> Set[str]:
    """Inventory exact loose/packed ref records without resolving their OIDs."""

    names = set(read_packed_refs(repo.pygit_dir))
    refs_root = repo.pygit_dir / "refs"
    if not refs_root.exists():
        return names
    if not refs_root.is_dir():
        raise RuntimeError("refs storage is not a directory")

    for path in sorted(refs_root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise RuntimeError(
                f"symbolic-link ref storage is not supported: {path.relative_to(repo.pygit_dir)}"
            )
        if not path.is_file():
            continue
        refname = "refs/" + path.relative_to(refs_root).as_posix()
        check_ref_format(refname)
        names.add(refname)
    return names


def ref_exists(repo: Repository, refname: str) -> bool:
    """Return whether one exact local ref record exists.

    This intentionally does not resolve the ref or inspect its target object.
    A loose ref shadows packed storage and counts as existing even when its
    recorded OID is missing or a symbolic target is dangling. Packed storage is
    parsed strictly so corruption remains distinguishable from a missing ref.
    """

    if not refname.startswith("refs/"):
        raise ValueError("--exists requires an exact ref name beginning with 'refs/'")
    relative = refname[len("refs/") :]
    path = repo.refs._path_under(repo.pygit_dir / "refs", relative)
    if path.is_file():
        return True
    return refname in read_packed_refs(repo.pygit_dir)


def _split_line_ending(line: bytes) -> Tuple[bytes, bytes]:
    if line.endswith(b"\r\n"):
        return line[:-2], b"\r\n"
    if line.endswith(b"\n"):
        return line[:-1], b"\n"
    return line, b""


def exclude_existing_refs(
    repo: Repository,
    lines: Iterable[bytes],
    *,
    pattern: Optional[str] = None,
) -> ExcludeExistingResult:
    """Filter stdin-style ref records to names absent from local ref storage.

    Each line may contain an arbitrary prefix followed by whitespace and a
    final refname token. A trailing ``^{}`` is stripped before matching and
    output. ``pattern`` is a literal head-match against that refname. Malformed
    input names are warned about and skipped; malformed local ref storage fails
    loudly through the strict packed/loose inventory.
    """

    existing = _stored_refnames(repo)
    output: List[bytes] = []
    warnings: List[str] = []

    for raw_line in lines:
        if not isinstance(raw_line, bytes):
            raise TypeError("exclude-existing input records must be bytes")
        body, ending = _split_line_ending(raw_line)
        if body.endswith(b"^{}"):
            body = body[:-3]

        match = re.search(rb"(\S+)$", body)
        if match is None:
            warnings.append("warning: malformed ref line ignored")
            continue

        raw_refname = match.group(1)
        try:
            refname = raw_refname.decode("utf-8")
        except UnicodeDecodeError:
            warnings.append("warning: non-UTF-8 refname ignored")
            continue

        if pattern is not None and not refname.startswith(pattern):
            continue

        try:
            check_ref_format(refname)
        except ValueError as exc:
            warnings.append(f"warning: invalid refname {refname!r} ignored: {exc}")
            continue

        if refname in existing:
            continue
        output.append(body + ending)

    return ExcludeExistingResult(b"".join(output), tuple(warnings))


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
