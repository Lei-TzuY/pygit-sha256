"""Installed CLI router.

``cat-file`` is handled here as one coherent command so its original single-
object modes and newer batch modes share the same object-ish resolver. Other
commands continue through the existing dispatch stack unchanged.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from .cat_file import inspect_object, object_exists
from .command import main as command_main
from .entrypoint import _find_repo
from .objects import CommitObject, TreeObject


def _run_cat_file(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit cat-file",
        description="Inspect SHA-256 objects and tree paths.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("-t", "--type", action="store_true", help="show object type")
    mode.add_argument("-s", "--size", action="store_true", help="show object size")
    mode.add_argument("-p", "--pretty", action="store_true", help="pretty-print object content")
    mode.add_argument("-e", "--exists", action="store_true", help="test whether OBJECT exists")
    mode.add_argument("--batch", action="store_true", help="read object names from stdin and emit header plus raw content")
    mode.add_argument("--batch-check", action="store_true", help="read object names from stdin and emit metadata only")
    parser.add_argument("object", nargs="?", metavar="OBJECT")
    args = parser.parse_args(list(argv))
    repo = _find_repo()

    if args.batch or args.batch_check:
        if args.object is not None:
            parser.error("batch modes read object names from stdin")
        output = sys.stdout.buffer
        for raw in sys.stdin:
            expression = raw.rstrip("\r\n")
            if not expression:
                output.write(b" missing\n")
                continue
            try:
                record = inspect_object(repo, expression)
            except (KeyError, ValueError, RuntimeError):
                output.write(expression.encode("utf-8") + b" missing\n")
                continue

            header = f"{record.oid} {record.type_name} {record.size}\n".encode("ascii")
            output.write(header)
            if args.batch:
                output.write(record.content)
                output.write(b"\n")
        return 0

    if not args.object:
        parser.error("single-object modes require OBJECT")
    if args.exists:
        return 0 if object_exists(repo, args.object) else 1

    record = inspect_object(repo, args.object)
    if args.type:
        print(record.type_name)
        return 0
    if args.size:
        print(record.size)
        return 0

    obj = repo.store.read(record.oid)
    if isinstance(obj, CommitObject):
        print(obj.pretty_print(record.oid))
    elif isinstance(obj, TreeObject):
        for entry in obj.entries:
            kind = "tree" if entry.is_dir else "blob"
            print(f"{entry.mode} {kind} {entry.sha}\t{entry.name}")
    else:
        sys.stdout.buffer.write(record.content)
    return 0


def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] == "cat-file":
        try:
            code = _run_cat_file(argv[1:])
        except (RuntimeError, ValueError, KeyError, FileNotFoundError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            code = 1
        if code:
            raise SystemExit(code)
        return
    command_main()
