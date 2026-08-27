"""Modern ``pygit clone`` wrapper that records initial branch tracking."""

from __future__ import annotations

import argparse
from typing import Sequence

from .repo import Repository
from .tracking import configure_clone_tracking


def run_clone(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit clone",
        description="Clone a smart HTTP Git repository.",
    )
    parser.add_argument("url", metavar="URL")
    parser.add_argument("directory", nargs="?", metavar="DIR")
    parser.add_argument(
        "-b",
        "--branch",
        metavar="BRANCH",
        help="point HEAD to the specified branch after cloning",
    )
    parser.add_argument(
        "--single-branch",
        action="store_true",
        help="clone only the history leading to one branch",
    )
    parser.add_argument(
        "--depth",
        type=int,
        metavar="DEPTH",
        help="create a shallow clone with truncated depth",
    )
    args = parser.parse_args(list(argv))

    repo = Repository.clone(
        args.url,
        args.directory,
        depth=args.depth,
        branch_name=args.branch,
        single_branch=args.single_branch,
    )
    branch = repo.refs.current_branch()
    if branch:
        configure_clone_tracking(repo, branch, remote="origin")
    print(f"Cloned {args.url} into {repo.worktree}")
    return 0
