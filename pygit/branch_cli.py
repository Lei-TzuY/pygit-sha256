"""Modern ``pygit branch`` grammar with upstream tracking setup."""

from __future__ import annotations

import argparse
from typing import Sequence

from .tracking import (
    configure_new_branch_tracking,
    find_repo,
    move_branch_upstream,
    resolve_tracking_source,
    set_branch_upstream,
    unset_branch_upstream,
)


def run_branch(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit branch",
        description="List, create, rename, delete, and configure branches.",
    )
    parser.add_argument("-a", "--all", action="store_true", help="list local and remote branches")
    parser.add_argument("-d", "--delete", action="store_true", help="delete a branch")
    parser.add_argument("-m", "--move", action="store_true", help="rename a branch")
    track_group = parser.add_mutually_exclusive_group()
    track_group.add_argument(
        "-t",
        dest="track",
        action="store_const",
        const="direct",
        help="set direct upstream tracking when creating a branch",
    )
    track_group.add_argument(
        "--track",
        dest="track",
        choices=("direct", "inherit"),
        metavar="{direct,inherit}",
        help="set upstream tracking when creating a branch",
    )
    track_group.add_argument(
        "--no-track",
        action="store_true",
        help="do not configure an upstream when creating a branch",
    )
    upstream_group = parser.add_mutually_exclusive_group()
    upstream_group.add_argument(
        "-u",
        "--set-upstream-to",
        metavar="UPSTREAM",
        help="set the upstream of an existing branch",
    )
    upstream_group.add_argument(
        "--unset-upstream",
        action="store_true",
        help="remove upstream configuration from a branch",
    )
    parser.add_argument("--contains", metavar="COMMIT")
    parser.add_argument("--no-contains", metavar="COMMIT")
    parser.add_argument("--merged", nargs="?", const="HEAD", metavar="COMMIT")
    parser.add_argument("--no-merged", nargs="?", const="HEAD", metavar="COMMIT")
    parser.add_argument("names", nargs="*", metavar="BRANCH")
    normalized = ["--track=direct" if token == "--track" else token for token in argv]
    args = parser.parse_args(normalized)

    repo = find_repo()

    if args.set_upstream_to:
        if len(args.names) > 1:
            parser.error("--set-upstream-to accepts at most one branch name")
        branch = args.names[0] if args.names else repo.refs.current_branch()
        if not branch:
            raise RuntimeError("cannot set upstream for detached HEAD")
        if not repo.refs.get_branch(branch):
            raise KeyError(f"Unknown branch: '{branch}'")
        source = resolve_tracking_source(repo, args.set_upstream_to)
        if source is None:
            raise KeyError(f"Unknown upstream branch: '{args.set_upstream_to}'")
        set_branch_upstream(repo, branch, source)
        print(f"branch '{branch}' set up to track '{source.display}'.")
        return 0

    if args.unset_upstream:
        if len(args.names) > 1:
            parser.error("--unset-upstream accepts at most one branch name")
        branch = args.names[0] if args.names else repo.refs.current_branch()
        if not branch:
            raise RuntimeError("cannot unset upstream for detached HEAD")
        if not repo.refs.get_branch(branch):
            raise KeyError(f"Unknown branch: '{branch}'")
        unset_branch_upstream(repo, branch)
        return 0

    if args.delete:
        if not args.names:
            parser.error("branch name required with -d")
        for branch in args.names:
            repo.branch(branch, delete=True)
            unset_branch_upstream(repo, branch)
            print(f"Deleted branch '{branch}'.")
        return 0

    if args.move:
        if len(args.names) == 1:
            old = repo.refs.current_branch()
            new = args.names[0]
            if not old:
                raise RuntimeError("cannot rename the current branch from detached HEAD")
        elif len(args.names) == 2:
            old, new = args.names
        else:
            parser.error("-m requires NEW or OLD NEW")
        repo.branch(old, rename=new)
        move_branch_upstream(repo, old, new)
        print(f"Renamed branch '{old}' to '{new}'.")
        return 0

    if args.names:
        if len(args.names) > 2:
            parser.error("branch creation accepts BRANCH [START_POINT]")
        name = args.names[0]
        start_point = args.names[1] if len(args.names) == 2 else "HEAD"
        repo.branch(name, start_point=start_point)
        tracked = configure_new_branch_tracking(
            repo,
            name,
            start_point,
            track=args.track,
            no_track=args.no_track,
        )
        print(f"Created branch '{name}'.")
        if tracked and tracked != "inherit":
            print(f"branch '{name}' set up to track '{tracked}'.")
        return 0

    branches = repo.branch(
        contains=args.contains,
        no_contains=args.no_contains,
        merged=args.merged,
        no_merged=args.no_merged,
    ) or []
    current = repo.refs.current_branch()
    for branch in branches:
        prefix = "* " if branch == current else "  "
        print(f"{prefix}{branch}")
    if args.all:
        for remote_branch in repo.list_remote_branches():
            print(f"  {remote_branch}")
    return 0
