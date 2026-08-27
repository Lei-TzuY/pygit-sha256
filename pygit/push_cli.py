"""Modern ``pygit push`` with Git-style default remote precedence."""

from __future__ import annotations

import argparse
from typing import Sequence

from .remote_ops import resolve_push_remote
from .tracking import TrackingSource, find_repo, set_branch_upstream


def run_push(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit push",
        description="Update a remote branch from the current local branch.",
    )
    parser.add_argument("repository", nargs="?", metavar="REPOSITORY")
    parser.add_argument("-f", "--force", action="store_true", help="force a non-fast-forward update")
    parser.add_argument(
        "-u",
        "--set-upstream",
        action="store_true",
        help="set the current branch's upstream after a successful push",
    )
    args = parser.parse_args(list(argv))

    repo = find_repo()
    branch = repo.refs.current_branch()
    if not branch:
        raise RuntimeError("cannot push from detached HEAD")

    remote = resolve_push_remote(repo, args.repository)
    result = repo.push(remote, force=args.force)

    if args.set_upstream:
        oid = str(result.get("sha") or repo.refs.resolve_head() or "")
        if not oid:
            raise RuntimeError("cannot set upstream without a branch tip")
        set_branch_upstream(repo, branch, TrackingSource(remote, branch, oid))

    print(
        f"Push result: {result['status']} "
        f"{result['remote']}/{result['branch']} ({result['objects']} objects)"
    )
    return 0
