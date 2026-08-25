"""
Reference querying and validation helpers.

This module backs ``for-each-ref`` and ``check-ref-format`` without growing the
already-large porcelain CLI implementation.  It operates on pygit's native
64-hex SHA-256 object IDs.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Union

from .objects import CommitObject, TagObject
from .plumbing import ancestor_distances, is_ancestor, list_refs, peel_oid, resolve_commit
from .repo import Repository
from .revision import resolve_revision


_FORBIDDEN_REF_CHARS = frozenset(" ~^:?*[\\")
_ATOM_RE = re.compile(r"%\(([^)]+)\)")
_HEX_ESCAPE_RE = re.compile(r"%([0-9a-fA-F]{2})")


@dataclass(frozen=True)
class RefRecord:
    oid: str
    refname: str
    peeled_oid: str
    object_type: str
    obj: object

    @property
    def short_name(self) -> str:
        for prefix in ("refs/heads/", "refs/tags/", "refs/remotes/"):
            if self.refname.startswith(prefix):
                return self.refname[len(prefix):]
        return self.refname


def _match_pattern(refname: str, pattern: str) -> bool:
    """Match Git for-each-ref style full-ref patterns."""
    if any(ch in pattern for ch in "*?["):
        return fnmatch.fnmatchcase(refname, pattern)
    pattern = pattern.rstrip("/")
    return refname == pattern or refname.startswith(pattern + "/")


def read_ref_patterns(lines: Iterable[str]) -> List[str]:
    """Read newline-delimited ``for-each-ref --stdin`` patterns.

    Only the record terminator is removed; leading/trailing spaces remain part
    of the pattern. Empty records are ignored, matching native Git's stdin
    pattern mode.
    """

    patterns: List[str] = []
    for raw in lines:
        pattern = raw.rstrip("\r\n")
        if pattern:
            patterns.append(pattern)
    return patterns


def _record(repo: Repository, oid: str, refname: str) -> RefRecord:
    obj = repo.store.read(oid)
    peeled = peel_oid(repo, oid)
    type_name = obj.type_name.decode("ascii", "replace")
    return RefRecord(oid, refname, peeled, type_name, obj)


def _points_at_oids(repo: Repository, record: RefRecord) -> set[str]:
    """Return every object a ref points at while recursively peeling tags.

    Native ``for-each-ref --points-at`` considers intermediate annotated-tag
    targets too. For ``ref -> tag2 -> tag1 -> commit``, queries for ``tag2``,
    ``tag1``, or the final commit must therefore all match the ref.
    """
    result: set[str] = set()
    current = record.oid
    while True:
        if current in result:
            raise RuntimeError(f"Tag cycle while peeling {record.oid}")
        result.add(current)
        if current == record.peeled_oid:
            return result
        obj = repo.store.read(current)
        if not isinstance(obj, TagObject):
            return result
        current = obj.target_sha


def _as_commit(repo: Repository, record: RefRecord) -> Optional[str]:
    try:
        obj = repo.store.read(record.peeled_oid)
    except (KeyError, ValueError):
        return None
    return record.peeled_oid if isinstance(obj, CommitObject) else None


def query_refs(
    repo: Repository,
    *,
    patterns: Sequence[str] = (),
    exclude_patterns: Sequence[str] = (),
    sort_keys: Sequence[str] = (),
    count: Optional[int] = None,
    points_at: Sequence[str] = (),
    contains: Optional[str] = None,
    no_contains: Optional[str] = None,
    merged: Optional[str] = None,
    no_merged: Optional[str] = None,
) -> List[RefRecord]:
    """
    Return refs after Git-style object, pattern, graph, sort, and count filters.

    ``patterns`` are inclusive full-ref prefix/glob selectors. Repeated
    ``exclude_patterns`` use the same matcher and remove matching refs after
    inclusion but before object inspection, graph predicates, sorting, or count
    limiting. Exclusions therefore compose as an OR filter without forcing
    excluded broken-object refs through metadata loading.

    ``points_at`` accepts arbitrary object-ish expressions. A ref matches when
    any object in its direct/annotated-tag peel chain equals a requested object.
    Multiple point targets therefore compose as an OR filter, matching native
    ``for-each-ref`` behavior including nested annotated tags.

    ``contains=X`` keeps refs whose tip contains X in its ancestry.
    ``merged=X`` keeps refs whose tip is already reachable from X.
    Negative forms invert those predicates. Annotated tags are peeled before
    graph predicates are evaluated.
    """
    if count is not None and count < 0:
        raise ValueError("--count must be non-negative")

    records = [
        _record(repo, oid, refname)
        for oid, refname in list_refs(repo)
        if (not patterns or any(_match_pattern(refname, p) for p in patterns))
        and not any(_match_pattern(refname, p) for p in exclude_patterns)
    ]

    point_targets = {resolve_revision(repo, expression) for expression in points_at}
    if point_targets:
        records = [
            record
            for record in records
            if point_targets.intersection(_points_at_oids(repo, record))
        ]

    contains_sha = resolve_commit(repo, contains) if contains else None
    no_contains_sha = resolve_commit(repo, no_contains) if no_contains else None
    merged_sha = resolve_commit(repo, merged) if merged else None
    no_merged_sha = resolve_commit(repo, no_merged) if no_merged else None

    merged_ancestors = ancestor_distances(repo, merged_sha) if merged_sha else None
    no_merged_ancestors = ancestor_distances(repo, no_merged_sha) if no_merged_sha else None

    filtered: List[RefRecord] = []
    graph_filter_active = any(
        value is not None
        for value in (contains_sha, no_contains_sha, merged_sha, no_merged_sha)
    )
    for record in records:
        commit_sha = _as_commit(repo, record)
        if graph_filter_active and commit_sha is None:
            continue
        assert commit_sha is not None or not graph_filter_active

        if contains_sha and not is_ancestor(repo, contains_sha, commit_sha):
            continue
        if no_contains_sha and is_ancestor(repo, no_contains_sha, commit_sha):
            continue
        if merged_ancestors is not None and commit_sha not in merged_ancestors:
            continue
        if no_merged_ancestors is not None and commit_sha in no_merged_ancestors:
            continue
        filtered.append(record)

    # Python's sort is stable. Applying keys in command-line order makes the
    # last --sort key the primary key, matching git for-each-ref.
    for key_spec in sort_keys:
        descending = key_spec.startswith("-")
        key_name = key_spec[1:] if descending else key_spec
        filtered.sort(
            key=lambda item, name=key_name: _sort_value(item, name),
            reverse=descending,
        )

    if count is not None:
        filtered = filtered[:count]
    return filtered


def _sort_value(record: RefRecord, key: str) -> Union[int, str]:
    if key == "refname":
        return record.refname
    if key == "objectname":
        return record.oid
    if key == "objecttype":
        return record.object_type
    if key in {"authordate", "committerdate", "taggerdate", "creatordate"}:
        return _date_value(record, key)
    raise ValueError(f"Unsupported sort field: {key!r}")


def _date_value(record: RefRecord, key: str) -> int:
    obj = record.obj
    if key == "authordate":
        return obj.author.timestamp if isinstance(obj, CommitObject) else 0
    if key == "committerdate":
        return obj.committer.timestamp if isinstance(obj, CommitObject) else 0
    if key == "taggerdate":
        return obj.tagger.timestamp if isinstance(obj, TagObject) else 0
    if isinstance(obj, TagObject):
        return obj.tagger.timestamp
    if isinstance(obj, CommitObject):
        return obj.committer.timestamp
    return 0


def _subject(message: str) -> str:
    for line in message.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _identity_atom(record: RefRecord, family: str, field: str) -> str:
    obj = record.obj
    identity = None
    if family == "author" and isinstance(obj, CommitObject):
        identity = obj.author
    elif family == "committer" and isinstance(obj, CommitObject):
        identity = obj.committer
    elif family == "tagger" and isinstance(obj, TagObject):
        identity = obj.tagger
    elif family == "creator":
        if isinstance(obj, CommitObject):
            identity = obj.committer
        elif isinstance(obj, TagObject):
            identity = obj.tagger

    if identity is None:
        return ""
    if field == "name":
        return identity.name
    if field == "email":
        return f"<{identity.email}>"
    if field == "date":
        return str(identity.timestamp)
    raise ValueError(f"Unsupported identity field: {family}{field}")


def _atom(record: RefRecord, atom: str) -> str:
    if atom == "refname":
        return record.refname
    if atom == "refname:short":
        return record.short_name
    if atom == "objectname":
        return record.oid
    if atom == "objectname:short":
        return record.oid[:12]
    if atom.startswith("objectname:short="):
        width_text = atom.split("=", 1)[1]
        if not width_text.isdigit() or int(width_text) <= 0:
            raise ValueError(f"Invalid objectname short width: {width_text!r}")
        return record.oid[: int(width_text)]
    if atom == "objecttype":
        return record.object_type

    obj = record.obj
    if atom in {"subject", "contents:subject"}:
        if isinstance(obj, (CommitObject, TagObject)):
            return _subject(obj.message)
        return ""

    mappings = {
        "authorname": ("author", "name"),
        "authoremail": ("author", "email"),
        "authordate:unix": ("author", "date"),
        "committername": ("committer", "name"),
        "committeremail": ("committer", "email"),
        "committerdate:unix": ("committer", "date"),
        "taggername": ("tagger", "name"),
        "taggeremail": ("tagger", "email"),
        "taggerdate:unix": ("tagger", "date"),
        "creatorname": ("creator", "name"),
        "creatoremail": ("creator", "email"),
        "creatordate:unix": ("creator", "date"),
    }
    if atom in mappings:
        family, field = mappings[atom]
        return _identity_atom(record, family, field)

    raise ValueError(f"Unsupported format atom: %({atom})")


def format_ref(record: RefRecord, format_string: str) -> str:
    """Expand a focused, useful subset of git-for-each-ref format atoms."""
    rendered = _ATOM_RE.sub(lambda match: _atom(record, match.group(1)), format_string)
    rendered = _HEX_ESCAPE_RE.sub(
        lambda match: chr(int(match.group(1), 16)),
        rendered,
    )
    return rendered.replace("%%", "%")


def normalize_refname(refname: str) -> str:
    """Remove leading and repeated slashes, as check-ref-format --normalize does."""
    return "/".join(part for part in refname.split("/") if part)


def check_ref_format(
    refname: str,
    *,
    allow_onelevel: bool = False,
    branch: bool = False,
    normalize: bool = False,
) -> str:
    """
    Validate the safety-relevant Git refname rules and return the checked name.

    The rules reject traversal-like/delimiter syntax, control characters,
    reserved ``@{`` syntax, dot components, and ``.lock`` suffixes.
    """
    candidate = normalize_refname(refname) if normalize else refname

    if branch:
        allow_onelevel = True
        if candidate.startswith("-"):
            raise ValueError("branch name cannot begin with '-'")

    if not candidate:
        raise ValueError("reference name must not be empty")
    if candidate == "@":
        raise ValueError("'@' is not a valid reference name")
    if candidate.startswith("/") or candidate.endswith("/") or "//" in candidate:
        raise ValueError("reference name contains an empty path component")
    if not allow_onelevel and "/" not in candidate:
        raise ValueError("one-level reference names require --allow-onelevel")
    if ".." in candidate:
        raise ValueError("reference name cannot contain '..'")
    if "@{" in candidate:
        raise ValueError("reference name cannot contain '@{'")
    if candidate.endswith("."):
        raise ValueError("reference name cannot end with '.'")

    for component in candidate.split("/"):
        if component.startswith("."):
            raise ValueError("reference path components cannot begin with '.'")
        if component.endswith(".lock"):
            raise ValueError("reference path components cannot end with '.lock'")
        if not component:
            raise ValueError("reference name contains an empty path component")

    for char in candidate:
        code = ord(char)
        if code < 0x20 or code == 0x7F or char in _FORBIDDEN_REF_CHARS:
            raise ValueError(f"reference name contains forbidden character {char!r}")

    return candidate
