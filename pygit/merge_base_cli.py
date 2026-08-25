"""Stable command-line adapter for ``pygit merge-base`` graph modes."""

from __future__ import annotations

import argparse
from typing import Sequence

from .entrypoint import _find_repo
from .fork_point import fork_point
from .graph_query import independent_commits, merge_bases_many, octopus_merge_bases
from .plumbing import is_ancestor


def run_merge_base(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit merge-base",
        description="Find best common ancestors and reflog-aware fork points.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--is-ancestor",
        action="store_true",
        help="test whether the first commit is an ancestor of the second",
    )
    mode.add_argument(
        "--octopus",
        action="store_true",
        help="find common ancestors shared by every supplied commit",
    )
    mode.add_argument(
        "--independent",
        action="store_true",
        help="print commits not reachable from any other supplied commit",
    )
    mode.add_argument(
        "--fork-point",
        action="store_true",
        help="find where COMMIT forked from a current or historical tip of REF",
    )
    parser.add_argument(
        "-a",
        "--all",
        action="store_true",
        help="print all best merge bases instead of only the first",
    )
    parser.add_argument("commit", nargs="+", metavar="COMMIT")
    args = parser.parse_args(list(argv))

    repo = _find_repo()
    if args.fork_point:
        if args.all or len(args.commit) not in {1, 2}:
            parser.error("--fork-point requires REF [COMMIT] and cannot use --all")
        point = fork_point(
            repo,
            args.commit[0],
            args.commit[1] if len(args.commit) == 2 else "HEAD",
        )
        if point is None:
            return 1
        print(point)
        return 0

    if args.is_ancestor:
        if args.all or len(args.commit) != 2:
            parser.error("--is-ancestor requires exactly two commits and cannot use --all")
        return 0 if is_ancestor(repo, args.commit[0], args.commit[1]) else 1

    if args.independent:
        if args.all:
            parser.error("--independent cannot be combined with --all")
        for oid in independent_commits(repo, args.commit):
            print(oid)
        return 0

    if len(args.commit) < 2:
        parser.error("merge-base requires at least two commits")

    bases = (
        octopus_merge_bases(repo, args.commit)
        if args.octopus
        else merge_bases_many(repo, args.commit)
    )
    if not bases:
        return 1
    for oid in bases if args.all else bases[:1]:
        print(oid)
    return 0
