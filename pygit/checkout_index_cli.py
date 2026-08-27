"""CLI adapter for stage-aware ``checkout-index`` plumbing."""

from __future__ import annotations

import argparse
import sys
from typing import List, Sequence, Union

from .checkout_index import checkout_index, checkout_index_temp
from .entrypoint import _find_repo


StageArgument = Union[int, str]


def _parse_stage(value: str) -> StageArgument:
    if value == "all":
        return value
    try:
        stage = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("stage must be 0, 1, 2, 3, or all") from exc
    if stage not in (0, 1, 2, 3):
        raise argparse.ArgumentTypeError("stage must be 0, 1, 2, 3, or all")
    return stage


def _read_stdin_paths(*, zero: bool) -> List[str]:
    data = sys.stdin.buffer.read()
    if not data:
        return []

    separator = b"\0" if zero else b"\n"
    records = data.split(separator)
    if records and records[-1] == b"":
        records.pop()

    paths: List[str] = []
    for record in records:
        if record == b"":
            raise ValueError("checkout-index --stdin received an empty path")
        paths.append(record.decode("utf-8", "surrogateescape"))
    return paths


def _format_temp_records(records, stage: StageArgument, *, zero: bool) -> None:
    separator = "\0" if zero else "\n"
    lines = []
    for record in records:
        if stage == "all":
            names = []
            for selected_stage in (1, 2, 3):
                temp_path = record.file_for(selected_stage)
                names.append(temp_path.name if temp_path is not None else ".")
            lines.append(f"{' '.join(names)}\t{record.path}")
        else:
            temp_path = record.file_for(int(stage))
            if temp_path is None:
                raise RuntimeError(
                    f"temporary checkout did not create stage {stage} for {record.path!r}"
                )
            lines.append(f"{temp_path.name}\t{record.path}")

    if lines:
        sys.stdout.write(separator.join(lines) + separator)


def run_checkout_index(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit checkout-index",
        description="Copy files from selected index stages or export them to temp files.",
    )
    parser.add_argument(
        "-a",
        "--all",
        action="store_true",
        help="checkout all entries at the selected stage",
    )
    parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="overwrite existing files (irrelevant in --temp mode)",
    )
    parser.add_argument(
        "--prefix",
        default="",
        metavar="PREFIX",
        help="write entries beneath PREFIX (ignored in --temp mode, matching Git)",
    )
    parser.add_argument(
        "--stage",
        type=_parse_stage,
        default=0,
        metavar="N|all",
        help="select stage 0/1/2/3, or all unmerged stages; all implies --temp",
    )
    parser.add_argument(
        "--temp",
        action="store_true",
        help="write selected index contents to unique temporary files and print mappings",
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="read pathnames from standard input instead of command-line path arguments",
    )
    parser.add_argument(
        "-z",
        "--null",
        action="store_true",
        help="use NUL for --stdin path input and temporary mapping record output",
    )
    parser.add_argument("path", nargs="*", metavar="PATH")
    args = parser.parse_args(list(argv))

    if args.all and args.path:
        parser.error("--all cannot be combined with explicit paths")
    if args.stdin and args.path:
        parser.error("--stdin cannot be combined with explicit paths")
    if args.stdin and args.all:
        parser.error("--stdin cannot be combined with --all")

    temp_mode = args.temp or args.stage == "all"
    if args.null and not (args.stdin or temp_mode):
        parser.error("-z/--null requires --stdin, --temp, or --stage=all")

    if args.stdin:
        try:
            selected_paths = _read_stdin_paths(zero=args.null)
        except ValueError as exc:
            parser.error(str(exc))
        # An empty stdin stream is a no-op, which keeps shell pipelines safe.
        if not selected_paths:
            return 0
    else:
        selected_paths = list(args.path)

    # Native checkout-index with no paths and without -a performs no work.
    # Preserve the Python API's stricter validation while keeping the CLI
    # script-friendly.
    if not args.all and not selected_paths:
        return 0

    repo = _find_repo()
    if temp_mode:
        records = checkout_index_temp(
            repo,
            selected_paths,
            all_entries=args.all,
            stage=args.stage,
        )
        _format_temp_records(records, args.stage, zero=args.null)
        return 0

    checkout_index(
        repo,
        selected_paths,
        all_entries=args.all,
        force=args.force,
        prefix=args.prefix,
        stage=int(args.stage),
    )
    return 0
