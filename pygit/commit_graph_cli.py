"""Modern nested command-line adapter for commit-graph maintenance."""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from .commit_graph import CommitGraph
from .commit_graph_reachability import (
    verify_commit_graph_coverage,
    write_reachable_commit_graph,
)
from .entrypoint import _find_repo


def run_commit_graph(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit commit-graph",
        description="Write or strictly verify pygit's commit-graph acceleration file.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    write_parser = sub.add_parser(
        "write",
        help="write the reachable commit graph atomically",
    )
    write_parser.add_argument(
        "--stdin-commits",
        action="store_true",
        help="read commit-ish roots from stdin instead of all repository refs plus HEAD",
    )
    verify_parser = sub.add_parser(
        "verify",
        help="verify graph structure and referenced commit metadata",
    )
    verify_parser.add_argument(
        "--reachable",
        action="store_true",
        help="also require coverage of every commit reachable from the selected roots",
    )
    verify_parser.add_argument(
        "--stdin-commits",
        action="store_true",
        help="read coverage roots from stdin; requires --reachable",
    )
    args = parser.parse_args(list(argv))

    if args.command == "verify" and args.stdin_commits and not args.reachable:
        verify_parser.error("--stdin-commits requires --reachable")

    repo = _find_repo()
    if args.command == "write":
        revisions = None
        if args.stdin_commits:
            revisions = [line.strip() for line in sys.stdin if line.strip()]
            if not revisions:
                raise ValueError("commit-graph --stdin-commits received no commits")
        path = write_reachable_commit_graph(repo, revisions)
        # The writer validates bytes before installation. Re-read against the
        # object database as a final end-to-end check before reporting success.
        CommitGraph(repo.pygit_dir).verify(repo.store)
        print(f"Wrote commit-graph to {path}")
        return 0

    if args.reachable:
        revisions = None
        if args.stdin_commits:
            revisions = [line.strip() for line in sys.stdin if line.strip()]
            if not revisions:
                raise ValueError(
                    "commit-graph verify --stdin-commits received no commits"
                )
        coverage = verify_commit_graph_coverage(repo, revisions)
        path = CommitGraph(repo.pygit_dir).graph_file
        print(
            f"{path}: ok "
            f"({coverage.expected_count} reachable, "
            f"{coverage.indexed_count} indexed, {coverage.extra_count} extra)"
        )
        return 0

    result = CommitGraph(repo.pygit_dir).verify(repo.store)
    print(
        f"{result.path}: ok "
        f"({result.commit_count} commits, max generation {result.max_generation})"
    )
    return 0
