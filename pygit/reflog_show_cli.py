"""CLI adapter for strict reflog inspection."""

from __future__ import annotations

import argparse
from typing import Sequence

from .entrypoint import _find_repo
from .reflog_show import format_reflog_entry, show_reflog


_DEFAULT_FORMAT = "%h %gD: %gs"


def run_reflog_show(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit reflog show",
        description="Show recorded ref movements without changing repository state.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        dest="all_refs",
        help="show every reflog below .pygit/logs in global timestamp order",
    )
    parser.add_argument(
        "-n",
        "--max-count",
        type=int,
        default=0,
        metavar="N",
        help="limit output to at most N records",
    )
    parser.add_argument(
        "--reverse",
        action="store_true",
        help="reverse the final output order",
    )
    parser.add_argument(
        "--format",
        default=_DEFAULT_FORMAT,
        metavar="FORMAT",
        help=(
            "format using %H %h %o %gD %gs %ct %r and %% "
            f"(default: {_DEFAULT_FORMAT!r})"
        ),
    )
    parser.add_argument(
        "ref",
        nargs="?",
        default="HEAD",
        metavar="REF",
        help="HEAD, refs/... name, or existing branch/remote short name",
    )
    args = parser.parse_args(list(argv))

    entries = show_reflog(
        _find_repo(),
        args.ref,
        all_refs=args.all_refs,
        max_count=args.max_count,
        reverse=args.reverse,
    )
    for entry in entries:
        print(format_reflog_entry(entry, args.format))
    return 0
