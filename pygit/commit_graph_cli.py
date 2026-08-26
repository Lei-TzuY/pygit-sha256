"""Modern nested command-line adapter for commit-graph maintenance."""

from __future__ import annotations

import argparse
from typing import Sequence

from .commit_graph import CommitGraph
from .entrypoint import _find_repo


def run_commit_graph(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit commit-graph",
        description="Write or strictly verify pygit's commit-graph acceleration file.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("write", help="write the reachable commit graph atomically")
    sub.add_parser("verify", help="verify graph structure and referenced commit metadata")
    args = parser.parse_args(list(argv))

    repo = _find_repo()
    if args.command == "write":
        path = repo.write_commit_graph()
        # The writer validates bytes before installation. Re-read against the
        # object database as a final end-to-end check before reporting success.
        CommitGraph(repo.pygit_dir).verify(repo.store)
        print(f"Wrote commit-graph to {path}")
        return 0

    result = CommitGraph(repo.pygit_dir).verify(repo.store)
    print(
        f"{result.path}: ok "
        f"({result.commit_count} commits, max generation {result.max_generation})"
    )
    return 0
