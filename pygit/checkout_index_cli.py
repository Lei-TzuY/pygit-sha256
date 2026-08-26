"""CLI adapter for stage-aware ``checkout-index`` plumbing."""

from __future__ import annotations

import argparse
from typing import Sequence

from .checkout_index import checkout_index
from .entrypoint import _find_repo


def run_checkout_index(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit checkout-index",
        description="Copy files from a selected index stage to the working tree.",
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
        help="overwrite existing files",
    )
    parser.add_argument(
        "--prefix",
        default="",
        metavar="PREFIX",
        help="write entries beneath PREFIX",
    )
    parser.add_argument(
        "--stage",
        type=int,
        choices=(0, 1, 2, 3),
        default=0,
        metavar="N",
        help="checkout index stage N: 1=base, 2=ours, 3=theirs (default: 0)",
    )
    parser.add_argument("path", nargs="*", metavar="PATH")
    args = parser.parse_args(list(argv))

    if args.all and args.path:
        parser.error("--all cannot be combined with explicit paths")

    checkout_index(
        _find_repo(),
        args.path,
        all_entries=args.all,
        force=args.force,
        prefix=args.prefix,
        stage=args.stage,
    )
    return 0
