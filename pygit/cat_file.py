"""Advanced ``cat-file`` plumbing for object inspection and batch queries.

Object-ish resolution is centralized in :mod:`pygit.revision` so cat-file and
rev-parse agree on refs, packed refs, abbreviated SHA-256 IDs, ancestry,
``REV:path`` walks, and ``^{type}`` peeling.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional, Tuple

from .objects import GitObject
from .repo import Repository
from .revision import resolve_revision


_BATCH_ATOMS = frozenset({"objectname", "objecttype", "objectsize", "rest"})
_BATCH_INPUT_SEPARATOR_RE = re.compile(r"\s+")
_HEX = frozenset("0123456789abcdef")


@dataclass(frozen=True)
class CatFileRecord:
    expression: str
    oid: str
    type_name: str
    size: int
    content: bytes


@dataclass(frozen=True)
class CatFileBatchCommand:
    """One parsed ``cat-file --batch-command`` request."""

    action: str
    expression: Optional[str] = None


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


def all_object_ids(repo: Repository) -> Tuple[str, ...]:
    """Return every known canonical SHA-256 object ID in deterministic order.

    The object store already merges loose and packed object names. This wrapper
    additionally filters incidental files that merely happen to live beneath a
    two-character loose-object directory and deduplicates loose/packed copies
    before sorting by object ID.
    """

    oids = set()
    for oid in repo.store.all_shas():
        if len(oid) != 64 or any(char not in _HEX for char in oid):
            continue
        oids.add(oid)
    return tuple(sorted(oids))


def _compile_batch_format(format_string: str) -> Tuple[Tuple[str, str], ...]:
    """Tokenize one batch format while validating supported atoms.

    ``%%`` is the only special non-atom escape in Git's batch format grammar;
    other percent sequences are preserved literally.
    """

    tokens = []
    literal = []
    index = 0
    while index < len(format_string):
        char = format_string[index]
        if char != "%":
            literal.append(char)
            index += 1
            continue
        if index + 1 < len(format_string) and format_string[index + 1] == "%":
            literal.append("%")
            index += 2
            continue
        if index + 1 < len(format_string) and format_string[index + 1] == "(":
            close = format_string.find(")", index + 2)
            if close < 0:
                raise ValueError("unterminated cat-file batch format atom")
            atom = format_string[index + 2 : close]
            if atom not in _BATCH_ATOMS:
                raise ValueError(f"unsupported cat-file batch format atom: %({atom})")
            if literal:
                tokens.append(("literal", "".join(literal)))
                literal.clear()
            tokens.append(("atom", atom))
            index = close + 1
            continue
        literal.append("%")
        index += 1
    if literal:
        tokens.append(("literal", "".join(literal)))
    return tuple(tokens)


def batch_format_uses_rest(format_string: Optional[str]) -> bool:
    """Return whether a custom batch format requests Git's ``%(rest)`` input split."""

    if format_string is None:
        return False
    return any(kind == "atom" and value == "rest" for kind, value in _compile_batch_format(format_string))


def split_batch_input(raw: str, format_string: Optional[str] = None) -> Tuple[str, str]:
    """Split one batch input record into object expression and ``%(rest)`` text.

    Without ``%(rest)`` the entire newline-stripped record is the object-ish,
    preserving spaces in revision paths. When ``%(rest)`` is requested, Git
    treats the first whitespace run as the separator and removes that run while
    preserving the remainder verbatim.
    """

    line = raw.rstrip("\r\n")
    if not batch_format_uses_rest(format_string):
        return line, ""
    match = _BATCH_INPUT_SEPARATOR_RE.search(line)
    if match is None:
        return line, ""
    return line[: match.start()], line[match.end() :]


def format_batch_record(
    record: CatFileRecord,
    format_string: str,
    *,
    rest: str = "",
) -> bytes:
    """Render one successful object record with a validated custom batch format."""

    values = {
        "objectname": record.oid,
        "objecttype": record.type_name,
        "objectsize": str(record.size),
        "rest": rest,
    }
    rendered = "".join(
        value if kind == "literal" else values[value]
        for kind, value in _compile_batch_format(format_string)
    )
    return (rendered + "\n").encode("utf-8")


def format_batch_object(
    repo: Repository,
    expression: str,
    *,
    contents: bool = False,
    format_string: Optional[str] = None,
    rest: str = "",
) -> bytes:
    """Render one batch response using the default or a custom header format.

    Missing or malformed object expressions are record-local failures and use
    Git's canonical ``<input> missing`` form regardless of custom formatting.
    Storage/I/O failures are intentionally not swallowed so callers can
    distinguish repository corruption from a missing object name.
    """

    if format_string is not None:
        _compile_batch_format(format_string)
    try:
        record = inspect_object(repo, expression)
    except (KeyError, ValueError, RuntimeError):
        return expression.encode("utf-8") + b" missing\n"

    if format_string is None:
        header = f"{record.oid} {record.type_name} {record.size}\n".encode("ascii")
    else:
        header = format_batch_record(record, format_string, rest=rest)
    if not contents:
        return header
    return header + record.content + b"\n"


def batch_all_objects(
    repo: Repository,
    *,
    contents: bool = False,
    format_string: Optional[str] = None,
) -> Iterable[bytes]:
    """Yield batch responses for every object known to loose or packed storage.

    Enumeration is independent of refs and reachability, so unreachable objects
    are included. Custom ``%(rest)`` expands to an empty string because there is
    no stdin record associated with an all-object query.
    """

    if format_string is not None:
        _compile_batch_format(format_string)
    for oid in all_object_ids(repo):
        yield format_batch_object(
            repo,
            oid,
            contents=contents,
            format_string=format_string,
        )


def parse_batch_command(raw: str) -> CatFileBatchCommand:
    """Parse one default-format ``--batch-command`` input line.

    The protocol deliberately uses an ASCII space between the command and its
    object argument. Everything after that first space is part of the object
    expression, including additional leading spaces, matching Git's command
    protocol rather than generic whitespace splitting.
    """

    line = raw.rstrip("\r\n")
    if not line:
        raise ValueError("empty cat-file batch command")

    if line == "flush":
        return CatFileBatchCommand("flush")
    if line.startswith("flush "):
        raise ValueError("flush takes no arguments")

    for action in ("info", "contents"):
        if line == action or line.startswith(action + "\t"):
            raise ValueError(f"{action} requires arguments")
        prefix = action + " "
        if line.startswith(prefix):
            expression = line[len(prefix) :]
            if not expression:
                raise ValueError(f"{action} requires arguments")
            return CatFileBatchCommand(action, expression)

    raise ValueError(f"unknown cat-file batch command: {line!r}")


def run_batch_commands(
    repo: Repository,
    commands: Iterable[str],
    *,
    buffered: bool = False,
    format_string: Optional[str] = None,
) -> Iterable[bytes]:
    """Execute ``--batch-command`` input and yield output flush chunks.

    Without buffering each object request yields one chunk immediately. With
    ``buffered=True`` responses accumulate until ``flush``; pending data is also
    yielded at clean end-of-input, as process exit flushes Git's buffered
    output. If parsing fails before a flush, pending buffered responses are not
    yielded. ``%(rest)`` is rendered as empty in command mode because the
    command protocol treats the complete text after ``info ``/``contents `` as
    the object expression rather than applying the batch-input rest split.
    """

    if format_string is not None:
        _compile_batch_format(format_string)
    pending = bytearray()
    for raw in commands:
        command = parse_batch_command(raw)
        if command.action == "flush":
            if not buffered:
                raise ValueError("flush is only valid with --buffer")
            yield bytes(pending)
            pending.clear()
            continue

        assert command.expression is not None
        payload = format_batch_object(
            repo,
            command.expression,
            contents=command.action == "contents",
            format_string=format_string,
        )
        if buffered:
            pending.extend(payload)
        else:
            yield payload

    if buffered and pending:
        yield bytes(pending)


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
