"""Raw commit-header presentation for ``rev-list --header``.

Git's ``--header`` mode is byte-oriented: each commit record begins with the
normal rev-list commit line, is followed by the commit object's stored payload
rendered in raw form, and is terminated by NUL.  This adapter intentionally
reads the exact validated store envelope so packed-only commits, signatures,
and extension headers are not reconstructed from ``CommitObject`` fields.
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from typing import Sequence

from .entrypoint import _find_repo
from .objects import CommitObject
from .rev_list_timestamp_cli import run_rev_list_timestamp


def _decorate_help(text: str) -> str:
    if "--header" in text:
        return text
    needle = "  --timestamp"
    index = text.find(needle)
    if index < 0:
        return text + "\n  --header             print raw commit contents with NUL record separators\n"
    line_end = text.find("\n", index)
    if line_end < 0:
        line_end = len(text)
    insertion = "\n  --header             print raw commit contents with NUL record separators"
    return text[:line_end] + insertion + text[line_end:]


def _run_captured(argv: Sequence[str]) -> tuple[int, str]:
    capture = io.StringIO()
    try:
        with redirect_stdout(capture):
            code = run_rev_list_timestamp(argv)
    except SystemExit as exc:
        raw_code = exc.code
        code = raw_code if isinstance(raw_code, int) else 1
    return code, capture.getvalue()


def _candidate_oid(line: str) -> str | None:
    """Extract the commit OID from plain, marked, or ``--timestamp`` lines."""
    fields = line.split(" ")
    if not fields:
        return None
    token = fields[0]
    if token.isdigit() and len(fields) >= 2:
        token = fields[1]
    if token[:1] in {"<", ">", "-"}:
        token = token[1:]
    lowered = token.lower()
    if len(lowered) != 64 or any(ch not in "0123456789abcdef" for ch in lowered):
        return None
    return lowered


def _raw_header_payload(store_bytes: bytes) -> bytes:
    """Convert an exact commit store envelope to Git's ``--header`` body."""
    nul = store_bytes.index(b"\x00")
    envelope = store_bytes[:nul]
    payload = store_bytes[nul + 1 :]
    if not envelope.startswith(b"commit "):
        raise ValueError("rev-list --header target is not a commit object")

    separator = payload.find(b"\n\n")
    if separator < 0:
        # Commit parsing already validates ordinary objects, but keep malformed
        # raw envelopes from silently producing an ambiguous header record.
        raise ValueError("commit payload has no header/message separator")
    headers = payload[: separator + 2]
    message = payload[separator + 2 :]
    if not message:
        return headers

    # Native raw display indents every logical message line by four spaces,
    # including blank lines. Preserve original bytes and newline termination.
    lines = message.splitlines(keepends=True)
    body = b"".join(b"    " + line for line in lines)
    if message and not lines:
        body = b"    " + message
    return headers + body


def run_rev_list_header(argv: Sequence[str]) -> int:
    """Run rev-list with native-style byte-faithful ``--header`` records."""
    args = list(argv)
    wants_header = "--header" in args
    cleaned = [arg for arg in args if arg != "--header"]

    if "--help" in cleaned or "-h" in cleaned:
        code, output = _run_captured(cleaned)
        print(_decorate_help(output), end="")
        return code

    if not wants_header:
        return run_rev_list_timestamp(cleaned)

    code, output = _run_captured(cleaned)
    if code:
        return code

    # --count suppresses commit records, so --header has nothing to decorate.
    if "--count" in cleaned:
        print(output, end="")
        return 0

    repo = _find_repo()
    target = getattr(sys.stdout, "buffer", None)
    if target is None:
        raise RuntimeError("rev-list --header requires a binary stdout stream")

    for line in output.splitlines():
        oid = _candidate_oid(line)
        if oid is None:
            target.write(line.encode("utf-8", "surrogateescape") + b"\n")
            continue
        try:
            obj = repo.store.read(oid)
        except (KeyError, RuntimeError, ValueError):
            obj = None
        if not isinstance(obj, CommitObject):
            target.write(line.encode("utf-8", "surrogateescape") + b"\n")
            continue

        store_bytes = repo.store.read_store_bytes(oid)
        target.write(line.encode("utf-8", "surrogateescape") + b"\n")
        target.write(_raw_header_payload(store_bytes))
        target.write(b"\x00")
    target.flush()
    return 0
