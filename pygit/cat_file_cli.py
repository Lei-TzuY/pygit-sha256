"""Stable command-line adapter for advanced :mod:`pygit.cat_file` plumbing."""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from .cat_file import format_batch_object, inspect_object, object_exists, run_batch_commands
from .entrypoint import _find_repo
from .objects import CommitObject, TreeObject


def run_cat_file(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit cat-file",
        description="Inspect SHA-256 objects and stream batch queries.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("-t", "--type", action="store_true", help="show object type")
    mode.add_argument("-s", "--size", action="store_true", help="show object size")
    mode.add_argument("-p", "--pretty", action="store_true", help="pretty-print object content")
    mode.add_argument("-e", "--exists", action="store_true", help="test whether OBJECT exists")
    mode.add_argument(
        "--batch",
        action="store_true",
        help="read object names from stdin and emit metadata plus raw content",
    )
    mode.add_argument(
        "--batch-check",
        action="store_true",
        help="read object names from stdin and emit metadata only",
    )
    mode.add_argument(
        "--batch-command",
        action="store_true",
        help="read info/contents/flush commands from stdin",
    )
    parser.add_argument(
        "--buffer",
        action="store_true",
        help="buffer batch output until flush or clean end-of-input",
    )
    parser.add_argument("object", nargs="?", metavar="OBJECT")
    args = parser.parse_args(list(argv))

    is_batch = args.batch or args.batch_check or args.batch_command
    if args.buffer and not is_batch:
        parser.error("--buffer requires --batch, --batch-check, or --batch-command")
    if is_batch and args.object is not None:
        parser.error("batch modes read object names or commands from stdin")

    repo = _find_repo()
    output = getattr(sys.stdout, "buffer", None)

    if args.batch_command:
        if output is None:
            raise RuntimeError("cat-file batch-command requires a binary stdout stream")
        for chunk in run_batch_commands(repo, sys.stdin, buffered=args.buffer):
            output.write(chunk)
            output.flush()
        return 0

    if args.batch or args.batch_check:
        if output is None:
            raise RuntimeError("cat-file batch modes require a binary stdout stream")
        for raw in sys.stdin:
            expression = raw.rstrip("\r\n")
            output.write(
                format_batch_object(
                    repo,
                    expression,
                    contents=args.batch,
                )
            )
            if not args.buffer:
                output.flush()
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
        if output is None:
            sys.stdout.write(record.content.decode("utf-8"))
        else:
            output.write(record.content)
    return 0
