"""CLI adapter for reflog-aware ``merge-base --fork-point``."""

from __future__ import annotations

import argparse
from typing import Sequence

from .entrypoint import _find_repo
from .fork_point import fork_point


def run_fork_point(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit merge-base",
        description="Find a reflog-aware fork point for a rewritten ref.",
    )
    parser.add_argument(
        "--fork-point",
        action="store_true",
        help="find the unique best historical tip of REF reachable from COMMIT",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="not supported with --fork-point",
    )
    parser.add_argument("ref", metavar="REF")
    parser.add_argument("commit", nargs="?", default="HEAD", metavar="COMMIT")
    args = parser.parse_args(list(argv))

    if not args.fork_point:
        parser.error("this route requires --fork-point")
    if args.all:
        parser.error("--fork-point cannot be combined with --all")

    repo = _find_repo()
    oid = fork_point(repo, args.ref, args.commit)
    if oid is None:
        return 1
    print(oid)
    return 0
