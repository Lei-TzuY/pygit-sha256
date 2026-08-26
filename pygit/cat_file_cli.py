"""Stable command-line adapter for advanced :mod:`pygit.cat_file` plumbing."""

from __future__ import annotations

import argparse
import sys
from typing import BinaryIO, Dict, Iterable, List, Optional, Sequence, Tuple

from .cat_file import (
    batch_all_objects,
    batch_format_uses_rest,
    format_batch_object,
    inspect_object,
    object_exists,
    run_batch_commands,
    split_batch_input,
)
from .cat_file_symlink import (
    format_batch_object_follow_symlinks,
    run_batch_commands_follow_symlinks,
)
from .entrypoint import _find_repo
from .object_enumeration import iter_object_ids
from .objects import CommitObject, TreeObject


_BATCH_FORMAT_OPTIONS = {
    "--batch": "batch",
    "--batch-check": "batch_check",
    "--batch-command": "batch_command",
}


def _normalize_batch_formats(argv: Sequence[str]) -> Tuple[List[str], Dict[str, Optional[str]]]:
    """Extract only Git's attached ``--batch*=FORMAT`` optional arguments."""

    normalized: List[str] = []
    formats: Dict[str, Optional[str]] = {name: None for name in _BATCH_FORMAT_OPTIONS.values()}
    for token in argv:
        matched = False
        for option, name in _BATCH_FORMAT_OPTIONS.items():
            prefix = option + "="
            if token.startswith(prefix):
                formats[name] = token[len(prefix) :]
                normalized.append(option)
                matched = True
                break
        if not matched:
            normalized.append(token)
    return normalized, formats


def _nul_records(stream: BinaryIO, chunk_size: int = 8192) -> Iterable[str]:
    """Yield UTF-8 records from a NUL-delimited binary stream incrementally.

    The yielded string retains one trailing NUL so lower-level parsers can
    remove exactly the active protocol delimiter while preserving embedded
    CR/LF bytes as object-expression data.
    """

    pending = bytearray()
    while True:
        chunk = stream.read(chunk_size)
        if not chunk:
            break
        pending.extend(chunk)
        while True:
            boundary = pending.find(0)
            if boundary < 0:
                break
            record = bytes(pending[:boundary])
            del pending[: boundary + 1]
            yield record.decode("utf-8") + "\0"
    if pending:
        yield bytes(pending).decode("utf-8") + "\0"


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
        "--batch-all-objects",
        action="store_true",
        help="ignore stdin and emit every object known to loose or packed storage",
    )
    parser.add_argument(
        "--unordered",
        action="store_true",
        help="with --batch-all-objects, visit objects in storage-local rather than hash order",
    )
    parser.add_argument(
        "--buffer",
        action="store_true",
        help="buffer batch output until flush or clean end-of-input",
    )
    parser.add_argument(
        "--follow-symlinks",
        action="store_true",
        help="follow in-tree symlinks in REV:path batch expressions",
    )
    parser.add_argument(
        "-Z",
        action="store_true",
        dest="zero_framing",
        help="use NUL rather than newline delimiters for batch input and output",
    )
    parser.add_argument("object", nargs="?", metavar="OBJECT")
    normalized, formats = _normalize_batch_formats(argv)
    args = parser.parse_args(normalized)

    is_batch = args.batch or args.batch_check or args.batch_command
    if args.buffer and not is_batch:
        parser.error("--buffer requires --batch, --batch-check, or --batch-command")
    if args.batch_all_objects and not is_batch:
        parser.error("--batch-all-objects requires --batch, --batch-check, or --batch-command")
    if args.unordered and not args.batch_all_objects:
        parser.error("--unordered requires --batch-all-objects")
    if args.follow_symlinks and not is_batch:
        parser.error("--follow-symlinks requires --batch, --batch-check, or --batch-command")
    if args.zero_framing and not is_batch:
        parser.error("-Z requires --batch, --batch-check, or --batch-command")
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
    output_terminator = b"\0" if args.zero_framing else b"\n"
    input_terminator = "\0" if args.zero_framing else "\n"

    if args.batch_all_objects:
        if output is None:
            raise RuntimeError("cat-file batch modes require a binary stdout stream")
        if args.unordered:
            for oid in iter_object_ids(repo, unordered=True):
                output.write(
                    format_batch_object(
                        repo,
                        oid,
                        contents=args.batch,
                        format_string=format_string,
                        record_terminator=output_terminator,
                    )
                )
                if not args.buffer:
                    output.flush()
        else:
            for payload in batch_all_objects(
                repo,
                contents=args.batch,
                format_string=format_string,
                record_terminator=output_terminator,
            ):
                output.write(payload)
                if not args.buffer:
                    output.flush()
        if args.buffer:
            output.flush()
        return 0

    if args.zero_framing:
        binary_input = getattr(sys.stdin, "buffer", None)
        if binary_input is None:
            raise RuntimeError("cat-file -Z requires a binary stdin stream")
        input_records: Iterable[str] = _nul_records(binary_input)
    else:
        input_records = sys.stdin

    if args.batch_command:
        if output is None:
            raise RuntimeError("cat-file batch-command requires a binary stdout stream")
        runner = (
            run_batch_commands_follow_symlinks
            if args.follow_symlinks
            else run_batch_commands
        )
        for chunk in runner(
            repo,
            input_records,
            buffered=args.buffer,
            format_string=format_string,
            input_terminator=input_terminator,
            output_terminator=output_terminator,
        ):
            output.write(chunk)
            output.flush()
        return 0

    if args.batch or args.batch_check:
        if output is None:
            raise RuntimeError("cat-file batch modes require a binary stdout stream")
        formatter = (
            format_batch_object_follow_symlinks
            if args.follow_symlinks
            else format_batch_object
        )
        for raw in input_records:
            expression, rest = split_batch_input(
                raw,
                format_string,
                record_terminator=input_terminator,
            )
            output.write(
                formatter(
                    repo,
                    expression,
                    contents=args.batch,
                    format_string=format_string,
                    rest=rest,
                    record_terminator=output_terminator,
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
