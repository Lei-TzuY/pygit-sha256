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


def format_batch_object(
    repo: Repository,
    expression: str,
    *,
    contents: bool = False,
) -> bytes:
    """Render one default-format batch response.

    Missing or malformed object expressions are record-local failures and use
    Git's ``<input> missing`` form. Storage/I/O failures are intentionally not
    swallowed so callers can distinguish repository corruption from a missing
    object name.
    """

    try:
        record = inspect_object(repo, expression)
    except (KeyError, ValueError, RuntimeError):
        return expression.encode("utf-8") + b" missing\n"

    header = f"{record.oid} {record.type_name} {record.size}\n".encode("ascii")
    if not contents:
        return header
    return header + record.content + b"\n"


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
) -> Iterable[bytes]:
    """Execute ``--batch-command`` input and yield output flush chunks.

    Without buffering each object request yields one chunk immediately. With
    ``buffered=True`` responses accumulate until ``flush``; pending data is also
    yielded at clean end-of-input, as process exit flushes Git's buffered
    output. If parsing fails before a flush, pending buffered responses are not
    yielded.
    """

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
