"""CLI adapter for reachability-aware reflog expiry."""

from __future__ import annotations

import argparse
from typing import Sequence

from .entrypoint import _find_repo
from .prune_cli import parse_expire
from .reflog_expire import expire_reflogs


def run_reflog_expire(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit reflog expire",
        description="Expire old reflog records while preserving current recovery paths.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        dest="all_refs",
        help="expire every reflog below .pygit/logs",
    )
    parser.add_argument(
        "--expire",
        default="90.days.ago",
        metavar="WHEN",
        help="expire all records older than WHEN (default: 90.days.ago)",
    )
    parser.add_argument(
        "--expire-unreachable",
        default="30.days.ago",
        metavar="WHEN",
        help="expire currently unreachable records older than WHEN (default: 30.days.ago)",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="show records that would expire without rewriting logs",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="print each expired record",
    )
    parser.add_argument(
        "ref",
        nargs="*",
        metavar="REF",
        help="HEAD or fully-qualified refs/... reflog (default: HEAD)",
    )
    args = parser.parse_args(list(argv))

    result = expire_reflogs(
        _find_repo(),
        args.ref,
        all_refs=args.all_refs,
        expire_before=parse_expire(args.expire),
        expire_unreachable_before=parse_expire(args.expire_unreachable),
        dry_run=args.dry_run,
    )

    if args.verbose or args.dry_run:
        for entry in result.entries:
            print(
                f"{entry.ref}\t{entry.old_oid}\t{entry.new_oid}\t"
                f"{entry.timestamp}\t{entry.reason}\t{entry.message}"
            )
    return 0
