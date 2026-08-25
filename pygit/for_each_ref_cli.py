"""Stable command-line adapter for structured ``for-each-ref`` queries."""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from .entrypoint import _find_repo
from .ref_query import format_ref, query_refs, read_ref_patterns


def run_for_each_ref(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit for-each-ref",
        description="Filter, sort, and format references.",
    )
    parser.add_argument(
        "--count",
        type=int,
        metavar="N",
        help="show at most N refs after filtering and sorting",
    )
    parser.add_argument(
        "--sort",
        action="append",
        default=[],
        metavar="KEY",
        help="sort by refname/objectname/objecttype/date field; prefix '-' for descending",
    )
    parser.add_argument(
        "--format",
        default="%(objectname) %(refname)",
        metavar="FORMAT",
        help="format output using %%(...)-style ref atoms",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="PATTERN",
        help="exclude refs matching a full-ref prefix or glob; may be repeated",
    )
    parser.add_argument(
        "--no-exclude",
        action="store_const",
        const=[],
        dest="exclude",
        help="clear previously supplied --exclude patterns",
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        dest="stdin_patterns",
        help="read newline-delimited inclusion patterns from stdin",
    )
    parser.add_argument(
        "--no-stdin",
        action="store_false",
        dest="stdin_patterns",
        help=argparse.SUPPRESS,
    )
    parser.set_defaults(stdin_patterns=False)
    parser.add_argument(
        "--points-at",
        action="append",
        default=[],
        metavar="OBJECT",
        help="only refs whose stored or peeled object matches OBJECT; may be repeated",
    )
    parser.add_argument(
        "--contains",
        nargs="?",
        const="HEAD",
        metavar="COMMIT",
        help="only refs whose tip contains COMMIT (default HEAD)",
    )
    parser.add_argument(
        "--no-contains",
        nargs="?",
        const="HEAD",
        metavar="COMMIT",
        help="only refs whose tip does not contain COMMIT",
    )
    parser.add_argument(
        "--merged",
        nargs="?",
        const="HEAD",
        metavar="COMMIT",
        help="only refs whose tip is reachable from COMMIT",
    )
    parser.add_argument(
        "--no-merged",
        nargs="?",
        const="HEAD",
        metavar="COMMIT",
        help="only refs whose tip is not reachable from COMMIT",
    )
    parser.add_argument("pattern", nargs="*", metavar="PATTERN")
    args = parser.parse_args(list(argv))

    if args.stdin_patterns and args.pattern:
        parser.error("--stdin cannot be combined with positional patterns")
    patterns = read_ref_patterns(sys.stdin) if args.stdin_patterns else args.pattern

    records = query_refs(
        _find_repo(),
        patterns=patterns,
        exclude_patterns=args.exclude,
        sort_keys=args.sort,
        count=args.count,
        points_at=args.points_at,
        contains=args.contains,
        no_contains=args.no_contains,
        merged=args.merged,
        no_merged=args.no_merged,
    )
    for record in records:
        print(format_ref(record, args.format))
    return 0
