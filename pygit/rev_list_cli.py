"""CLI adapter for advanced ``rev-list`` commit/object traversal."""

from __future__ import annotations

import argparse
from typing import Sequence

from .entrypoint import _find_repo
from .rev_list import rev_list, rev_list_objects


def run_rev_list(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit rev-list",
        description="List commits or their object closure without touching repository state.",
    )
    parser.add_argument("--all", action="store_true", help="start from every commit-ish ref")
    parser.add_argument("--count", action="store_true", help="print only the number of selected commits")
    parser.add_argument(
        "--objects",
        action="store_true",
        help="include the selected commits' required tree/blob object closure (OID-only output)",
    )
    parser.add_argument(
        "--left-right",
        action="store_true",
        help="mark commits from the left/right side of one A...B symmetric range",
    )
    parser.add_argument("--first-parent", action="store_true", help="follow only the first parent of merge commits")
    parser.add_argument("--topo-order", action="store_true", help="never show a parent before a selected child")
    parser.add_argument("--reverse", action="store_true", help="reverse the final selected commit order")
    parser.add_argument("--skip", type=int, default=0, metavar="N", help="skip the first N selected commits")
    parser.add_argument(
        "-n",
        "--max-count",
        type=int,
        default=0,
        metavar="N",
        help="limit output to at most N selected commits",
    )
    parser.add_argument(
        "revision",
        nargs="*",
        metavar="REV",
        help="positive REV, ^REV exclusion, A..B range, or one A...B symmetric range",
    )
    args = parser.parse_args(list(argv))

    if args.objects:
        if args.count:
            parser.error("--objects cannot be combined with --count")
        if args.left_right:
            parser.error("--objects cannot be combined with --left-right")
        objects = rev_list_objects(
            _find_repo(),
            args.revision,
            all_refs=args.all,
            first_parent=args.first_parent,
            topo_order=args.topo_order,
            reverse=args.reverse,
            skip=args.skip,
            max_count=args.max_count,
        )
        for entry in objects:
            print(entry.oid)
        return 0

    entries = rev_list(
        _find_repo(),
        args.revision,
        all_refs=args.all,
        first_parent=args.first_parent,
        topo_order=args.topo_order,
        reverse=args.reverse,
        skip=args.skip,
        max_count=args.max_count,
        left_right=args.left_right,
    )
    if args.count:
        print(len(entries))
        return 0

    for entry in entries:
        print(f"{entry.side or ''}{entry.oid}")
    return 0
