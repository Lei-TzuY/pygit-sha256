"""Timestamp presentation wrapper for ``rev-list --timestamp``.

The core rev-list selector intentionally remains unchanged.  This adapter layers
Git-style raw committer timestamps onto commit records after the existing
selection/presentation pipeline has produced them, so parent/child/boundary,
side-marker and object modes keep their established semantics.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from typing import Sequence

from .entrypoint import _find_repo
from .objects import CommitObject
from .rev_list_cli import run_rev_list


def _timestamp_for_commit(repo, oid: str) -> int:
    obj = repo.store.read(oid)
    if not isinstance(obj, CommitObject):
        raise RuntimeError(f"Object {oid} in rev-list timestamp output is not a commit")
    committer = getattr(obj, "committer", None)
    if committer is not None:
        return int(getattr(committer, "timestamp", 0))
    author = getattr(obj, "author", None)
    return int(getattr(author, "timestamp", 0)) if author is not None else 0


def _candidate_oid(line: str) -> tuple[str, str] | None:
    """Return (marker, oid) when a line begins with a commit-like OID token."""
    token = line.split(" ", 1)[0]
    marker = ""
    if token[:1] in {"<", ">", "-"}:
        marker, token = token[0], token[1:]
    lowered = token.lower()
    if len(lowered) != 64 or any(ch not in "0123456789abcdef" for ch in lowered):
        return None
    return marker, lowered


def _decorate_help(text: str) -> str:
    if "--timestamp" in text:
        return text
    needle = "  --boundary"
    index = text.find(needle)
    if index < 0:
        return text + "\n  --timestamp          print the raw commit timestamp\n"
    line_end = text.find("\n", index)
    if line_end < 0:
        line_end = len(text)
    insertion = "\n  --timestamp          print the raw commit timestamp"
    return text[:line_end] + insertion + text[line_end:]


def _run_captured(argv: Sequence[str]) -> tuple[int, str]:
    """Run the legacy adapter while preserving argparse's help exit protocol."""
    capture = io.StringIO()
    try:
        with redirect_stdout(capture):
            code = run_rev_list(argv)
    except SystemExit as exc:
        # argparse implements --help/-h by printing help and raising
        # SystemExit(0).  The timestamp wrapper must not lose that captured
        # output merely because control flow exits through argparse.
        raw_code = exc.code
        code = raw_code if isinstance(raw_code, int) else 1
    return code, capture.getvalue()


def run_rev_list_timestamp(argv: Sequence[str]) -> int:
    """Run rev-list, implementing native-style ``--timestamp`` presentation."""
    args = list(argv)
    wants_timestamp = "--timestamp" in args
    cleaned = [arg for arg in args if arg != "--timestamp"]

    # Route every installed rev-list invocation through this wrapper so help can
    # advertise the option even when --timestamp itself was not supplied.
    if "--help" in cleaned or "-h" in cleaned:
        code, output = _run_captured(cleaned)
        print(_decorate_help(output), end="")
        return code

    if not wants_timestamp:
        return run_rev_list(cleaned)

    code, output = _run_captured(cleaned)
    if code:
        return code

    # --count suppresses normal commit output in Git; --timestamp does not alter
    # that protocol.
    if "--count" in cleaned:
        print(output, end="")
        return 0

    repo = _find_repo()
    object_edge_mode = "--objects-edge" in cleaned

    for raw_line in output.splitlines():
        candidate = _candidate_oid(raw_line)
        if candidate is None:
            print(raw_line)
            continue
        marker, oid = candidate

        # Native --objects-edge emits its leading uninteresting edge records
        # without timestamps, even when --boundary is also present.
        if object_edge_mode and marker == "-":
            print(raw_line)
            continue

        try:
            obj = repo.store.read(oid)
        except (KeyError, RuntimeError, ValueError):
            print(raw_line)
            continue
        if not isinstance(obj, CommitObject):
            print(raw_line)
            continue

        print(f"{_timestamp_for_commit(repo, oid)} {raw_line}")
    return 0
