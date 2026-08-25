"""Stable installed command launcher.

Commands with binary-stdin or custom parsing needs are intercepted here before
the older argparse stack. Everything else delegates to :mod:`pygit.runtime`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from .entrypoint import _find_repo
from .fsck import fsck
from .hash_object import hash_object_data, write_object_data
from .runtime import main as runtime_main


def _stdin_bytes() -> bytes:
    binary = getattr(sys.stdin, "buffer", None)
    if binary is not None:
        return binary.read()
    data = sys.stdin.read()
    return data if isinstance(data, bytes) else data.encode("utf-8")


def _run_hash_object(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit hash-object",
        description="Compute SHA-256 object IDs from files or stdin.",
    )
    parser.add_argument(
        "-t",
        "--type",
        default="blob",
        choices=("blob", "tree", "commit", "tag"),
        dest="object_type",
        help="object type to hash (default: blob)",
    )
    parser.add_argument("-w", "--write", action="store_true", help="write objects to the repository")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--stdin", action="store_true", help="read one object payload from standard input")
    source.add_argument("--stdin-paths", action="store_true", help="read newline-delimited file paths from standard input")
    parser.add_argument("file", nargs="*", metavar="FILE")
    args = parser.parse_args(list(argv))

    if args.stdin and args.file:
        parser.error("--stdin cannot be combined with file arguments")
    if args.stdin_paths and args.file:
        parser.error("--stdin-paths cannot be combined with file arguments")
    if not args.stdin and not args.stdin_paths and not args.file:
        parser.error("hash-object requires FILE, --stdin, or --stdin-paths")

    repo = _find_repo() if args.write else None

    def process(data: bytes) -> None:
        oid = (
            write_object_data(repo, data, args.object_type)
            if repo is not None
            else hash_object_data(data, args.object_type)
        )
        print(oid)

    if args.stdin:
        process(_stdin_bytes())
        return 0

    if args.stdin_paths:
        for raw in sys.stdin:
            path = raw.rstrip("\r\n")
            if not path:
                continue
            process(Path(path).read_bytes())
        return 0

    for path in args.file:
        process(Path(path).read_bytes())
    return 0


def _run_fsck(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit fsck",
        description="Verify SHA-256 object storage and repository connectivity.",
    )
    scan = parser.add_mutually_exclusive_group()
    scan.add_argument("--full", action="store_true", help="check all loose and packed objects (the default)")
    scan.add_argument("--connectivity-only", action="store_true", help="walk only objects reachable from refs, index, and shallow roots")
    parser.add_argument("--unreachable", action="store_true", help="print every unreachable object instead of only dangling roots")
    parser.add_argument("--no-dangling", action="store_true", help="suppress dangling-object output")
    parser.add_argument("--strict", action="store_true", help="treat fsck warnings as a failing result")
    args = parser.parse_args(list(argv))

    repo = _find_repo()
    report = fsck(repo, connectivity_only=args.connectivity_only)

    for issue in sorted(
        report.issues,
        key=lambda item: (item.severity != "error", item.code, item.oid or "", item.source or ""),
    ):
        print(issue.render(), file=sys.stderr)

    if args.unreachable:
        selected = report.unreachable
        label = "unreachable"
    elif args.no_dangling:
        selected = set()
        label = "dangling"
    else:
        selected = report.dangling
        label = "dangling"

    for oid in sorted(selected):
        try:
            kind = repo.store.read(oid).type_name.decode("ascii", "replace")
        except Exception:
            kind = "object"
        print(f"{label} {kind} {oid}")

    failed = bool(report.errors) or (args.strict and bool(report.warnings))
    return 1 if failed else 0


def main() -> None:
    argv = sys.argv[1:]
    if not argv or argv[0] not in {"hash-object", "fsck"}:
        runtime_main()
        return

    try:
        if argv[0] == "hash-object":
            code = _run_hash_object(argv[1:])
        else:
            code = _run_fsck(argv[1:])
    except (RuntimeError, ValueError, KeyError, FileNotFoundError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        code = 1
    if code:
        raise SystemExit(code)
