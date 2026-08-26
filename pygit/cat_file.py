"""Advanced ``cat-file`` plumbing for object inspection and batch queries.

Object-ish resolution is centralized in :mod:`pygit.revision` so cat-file and
rev-parse agree on refs, packed refs, abbreviated SHA-256 IDs, ancestry,
``REV:path`` walks, and ``^{type}`` peeling.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional, Tuple

from .repo import Repository
from .revision import resolve_revision


_BATCH_ATOMS = frozenset({"objectname", "objecttype", "objectsize", "objectsize:disk", "rest"})
_BATCH_INPUT_SEPARATOR_RE = re.compile(r"\s+")
_HEX = frozenset("0123456789abcdef")


@dataclass(frozen=True)
class CatFileRecord:
    expression: str
    oid: str
    type_name: str
    size: int
    content: bytes
    disk_size: int


@dataclass(frozen=True)
class CatFileBatchCommand:
    """One parsed ``cat-file --batch-command`` request."""

    action: str
    expression: Optional[str] = None


def resolve_object(repo: Repository, expression: str) -> str:
    """Backward-compatible alias for the unified object-ish resolver."""
    return resolve_revision(repo, expression)


def object_disk_size(repo: Repository, oid: str) -> int:
    """Return the number of bytes used by the selected on-disk object copy.

    Loose objects use the compressed loose-file size. Packed objects use the
    exact encoded pack-entry width, including the entry header and compressed
    payload but excluding neighboring entries and the pack trailer. The lookup
    order mirrors :meth:`ObjectStore.read`: a loose copy wins over packed copies;
    when multiple packed copies exist, the first pack in deterministic path
    order is selected.
    """

    loose_path = repo.store.root / oid[:2] / oid[2:]
    if loose_path.is_file():
        return loose_path.stat().st_size

    from .pack import PackReader

    pack_dir = repo.store.root / "pack"
    if pack_dir.is_dir():
        for idx_file in sorted(pack_dir.glob("*.idx")):
            reader = PackReader(idx_file)
            if not reader.has_object(oid):
                continue
            _, payload_end = reader._load_pack_image()
            offset = reader._offsets[oid]
            return reader._entry_end(offset, payload_end) - offset

    raise KeyError(f"Object not found: {oid}")


def _split_store_bytes(store_bytes: bytes) -> Tuple[str, bytes]:
    """Extract the exact type name and payload from a validated object envelope."""
    try:
        nul = store_bytes.index(b"\x00")
        type_bytes, size_bytes = store_bytes[:nul].split(b" ", 1)
        declared_size = int(size_bytes)
        type_name = type_bytes.decode("ascii")
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("invalid object envelope") from exc

    content = store_bytes[nul + 1 :]
    if len(content) != declared_size:
        raise ValueError(
            f"Size mismatch: header says {declared_size}, got {len(content)}"
        )
    return type_name, content


def inspect_object(repo: Repository, expression: str) -> CatFileRecord:
    oid = resolve_revision(repo, expression)
    store_bytes = repo.store.read_store_bytes(oid)
    type_name, content = _split_store_bytes(store_bytes)
    return CatFileRecord(
        expression=expression,
        oid=oid,
        type_name=type_name,
        size=len(content),
        content=content,
        disk_size=object_disk_size(repo, oid),
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


def _strip_record_terminator(raw: str, record_terminator: str) -> str:
    if record_terminator not in {"\n", "\0"}:
        raise ValueError("record terminator must be newline or NUL")
    if record_terminator == "\0":
        return raw[:-1] if raw.endswith("\0") else raw
    return raw.rstrip("\r\n")


def _validate_output_terminator(record_terminator: bytes) -> None:
    if record_terminator not in {b"\n", b"\0"}:
        raise ValueError("record terminator must be newline or NUL")


def split_batch_input(
    raw: str,
    format_string: Optional[str] = None,
    *,
    record_terminator: str = "\n",
) -> Tuple[str, str]:
    """Split one batch input record into object expression and ``%(rest)`` text.

    Without ``%(rest)`` the complete delimiter-stripped record is the object-ish.
    With ``%(rest)``, the first whitespace run separates the object expression
    from rest text. Under NUL framing embedded newlines remain ordinary data.
    """

    line = _strip_record_terminator(raw, record_terminator)
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
    record_terminator: bytes = b"\n",
) -> bytes:
    """Render one successful object record with a validated custom batch format."""

    _validate_output_terminator(record_terminator)
    values = {
        "objectname": record.oid,
        "objecttype": record.type_name,
        "objectsize": str(record.size),
        "objectsize:disk": str(record.disk_size),
        "rest": rest,
    }
    rendered = "".join(
        value if kind == "literal" else values[value]
        for kind, value in _compile_batch_format(format_string)
    )
    return rendered.encode("utf-8") + record_terminator


def format_batch_object(
    repo: Repository,
    expression: str,
    *,
    contents: bool = False,
    format_string: Optional[str] = None,
    rest: str = "",
    record_terminator: bytes = b"\n",
) -> bytes:
    """Render one batch response using the default or a custom header format.

    Missing or malformed object expressions are record-local failures and use
    Git's canonical ``<input> missing`` form regardless of custom formatting.
    ``record_terminator`` controls only protocol framing; object bytes are never
    rewritten, even when they contain newlines or NULs.
    """

    _validate_output_terminator(record_terminator)
    if format_string is not None:
        _compile_batch_format(format_string)
    try:
        record = inspect_object(repo, expression)
    except (KeyError, ValueError, RuntimeError):
        return expression.encode("utf-8") + b" missing" + record_terminator

    if format_string is None:
        header = f"{record.oid} {record.type_name} {record.size}".encode("ascii") + record_terminator
    else:
        header = format_batch_record(
            record,
            format_string,
            rest=rest,
            record_terminator=record_terminator,
        )
    if not contents:
        return header
    return header + record.content + record_terminator


def batch_all_objects(
    repo: Repository,
    *,
    contents: bool = False,
    format_string: Optional[str] = None,
    record_terminator: bytes = b"\n",
) -> Iterable[bytes]:
    """Yield batch responses for every object known to loose or packed storage.

    Enumeration is independent of refs and reachability. Custom ``%(rest)`` is
    empty because there is no stdin record associated with an all-object query.
    """

    _validate_output_terminator(record_terminator)
    if format_string is not None:
        _compile_batch_format(format_string)
    for oid in all_object_ids(repo):
        yield format_batch_object(
            repo,
            oid,
            contents=contents,
            format_string=format_string,
            record_terminator=record_terminator,
        )


def parse_batch_command(
    raw: str,
    *,
    record_terminator: str = "\n",
) -> CatFileBatchCommand:
    """Parse one ``--batch-command`` protocol record."""

    line = _strip_record_terminator(raw, record_terminator)
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
    input_terminator: str = "\n",
    output_terminator: bytes = b"\n",
) -> Iterable[bytes]:
    """Execute ``--batch-command`` input and yield output flush chunks.

    Input and output delimiters are independent protocol parameters so callers
    can model newline or NUL framing without touching object content. With
    buffering, responses accumulate until ``flush`` or clean end-of-input.
    """

    _validate_output_terminator(output_terminator)
    if input_terminator not in {"\n", "\0"}:
        raise ValueError("record terminator must be newline or NUL")
    if format_string is not None:
        _compile_batch_format(format_string)
    pending = bytearray()
    for raw in commands:
        command = parse_batch_command(raw, record_terminator=input_terminator)
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
            record_terminator=output_terminator,
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
