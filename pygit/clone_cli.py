"""Modern ``pygit clone`` wrapper with Git-style remote metadata."""

from __future__ import annotations

import argparse
from typing import Sequence

from .clone_remote import clone_default_branch, configure_clone_remote
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
    single = parser.add_mutually_exclusive_group()
    single.add_argument(
        "--single-branch",
        dest="single_branch",
        action="store_true",
        help="clone only the history leading to one branch",
    )
    single.add_argument(
        "--no-single-branch",
        dest="single_branch",
        action="store_false",
        help="fetch all branch tips even when --depth is used",
    )
    parser.set_defaults(single_branch=None)
    parser.add_argument(
        "--depth",
        type=int,
        metavar="DEPTH",
        help="create a shallow clone with truncated depth",
    )
    args = parser.parse_args(list(argv))

    if args.depth is not None and args.depth <= 0:
        parser.error("--depth must be a positive integer")

    # Native Git makes --depth imply --single-branch unless the user explicitly
    # asks for --no-single-branch.
    single_branch = (
        args.single_branch
        if args.single_branch is not None
        else args.depth is not None
    )

    repo = Repository.clone(
        args.url,
        args.directory,
        depth=args.depth,
        branch_name=args.branch,
        single_branch=single_branch,
    )
    branch = repo.refs.current_branch()
    if branch:
        configure_clone_remote(
            repo,
            args.url,
            branch,
            remote="origin",
            default_branch=clone_default_branch(repo, "origin"),
            single_branch=single_branch,
        )
        configure_clone_tracking(repo, branch, remote="origin")
    print(f"Cloned {args.url} into {repo.worktree}")
    return 0
