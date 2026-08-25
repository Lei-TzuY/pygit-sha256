"""Stable command-line adapter for advanced :mod:`pygit.cat_file` plumbing."""

from __future__ import annotations

import argparse
import sys
from typing import Dict, List, Optional, Sequence, Tuple

from .cat_file import (
    batch_all_objects,
    batch_format_uses_rest,
    format_batch_object,
    inspect_object,
    object_exists,
    run_batch_commands,
    split_batch_input,
)
from .entrypoint import _find_repo
from .objects import CommitObject, TreeObject

_BATCH_FORMAT_OPTIONS = {"--batch": "batch", "--batch-check": "batch_check", "--batch-command": "batch_command"}


def _normalize_batch_formats(argv: Sequence[str]) -> Tuple[List[str], Dict[str, Optional[str]]]:
    normalized: List[str] = []
    formats: Dict[str, Optional[str]] = {name: None for name in _BATCH_FORMAT_OPTIONS.values()}
    for token in argv:
        matched = False
        for option, name in _BATCH_FORMAT_OPTIONS.items():
            prefix = option + "="
            if token.startswith(prefix):
                formats[name] = token[len(prefix):]
                normalized.append(option)
                matched = True
                break
        if not matched:
            normalized.append(token)
    return normalized, formats


def run_cat_file(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="pygit cat-file", description="Inspect SHA-256 objects and stream batch queries.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("-t", "--type", action="store_true")
    mode.add_argument("-s", "--size", action="store_true")
    mode.add_argument("-p", "--pretty", action="store_true")
    mode.add_argument("-e", "--exists", action="store_true")
    mode.add_argument("--batch", action="store_true")
    mode.add_argument("--batch-check", action="store_true")
    mode.add_argument("--batch-command", action="store_true")
    parser.add_argument("--batch-all-objects", action="store_true", help="ignore stdin and emit every loose or packed object")
    parser.add_argument("--buffer", action="store_true")
    parser.add_argument("object", nargs="?", metavar="OBJECT")
    normalized, formats = _normalize_batch_formats(argv)
    args = parser.parse_args(normalized)

    is_batch = args.batch or args.batch_check or args.batch_command
    if args.buffer and not is_batch:
        parser.error("--buffer requires --batch, --batch-check, or --batch-command")
    if args.batch_all_objects and not is_batch:
        parser.error("--batch-all-objects requires --batch, --batch-check, or --batch-command")
    if is_batch and args.object is not None:
        parser.error("batch modes read object names or commands from stdin")

    format_string: Optional[str] = None
    if args.batch:
        format_string = formats["batch"]
    elif args.batch_check:
        format_string = formats["batch_check"]
    elif args.batch_command:
        format_string = formats["batch_command"]
    if format_string is not None:
        batch_format_uses_rest(format_string)

    repo = _find_repo()
    output = getattr(sys.stdout, "buffer", None)

    if args.batch_all_objects:
        if output is None:
            raise RuntimeError("cat-file batch modes require a binary stdout stream")
        for payload in batch_all_objects(repo, contents=args.batch, format_string=format_string):
            output.write(payload)
            if not args.buffer:
                output.flush()
        if args.buffer:
            output.flush()
        return 0

    if args.batch_command:
        if output is None:
            raise RuntimeError("cat-file batch-command requires a binary stdout stream")
        for chunk in run_batch_commands(repo, sys.stdin, buffered=args.buffer, format_string=format_string):
            output.write(chunk)
            output.flush()
        return 0

    if args.batch or args.batch_check:
        if output is None:
            raise RuntimeError("cat-file batch modes require a binary stdout stream")
        for raw in sys.stdin:
            expression, rest = split_batch_input(raw, format_string)
            output.write(format_batch_object(repo, expression, contents=args.batch, format_string=format_string, rest=rest))
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
    elif output is None:
        sys.stdout.write(record.content.decode("utf-8"))
    else:
        output.write(record.content)
    return 0
