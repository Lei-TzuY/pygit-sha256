"""Modern ``pygit clone`` wrapper with Git-style remote metadata."""

from __future__ import annotations

import argparse
from typing import Sequence

from .clone_remote import clone_default_branch, configure_clone_remote
from .clone_shallow import clone_shallow_repository
from .fetch_protocol_v2 import protocol_v2_transport
from .repo import Repository
from .tracking import configure_clone_tracking


# Several established clone regression seams replace Repository.clone itself to
# observe the legacy call shape. Keep a stable reference to the real classmethod
# implementation so Phase204 can prefer true shallow transport in production
# without silently bypassing an explicit test/caller override.
_ORIGINAL_REPOSITORY_CLONE_FUNC = Repository.clone.__func__


def _repository_clone_overridden() -> bool:
    current = Repository.clone
    current_func = getattr(current, "__func__", current)
    return current_func is not _ORIGINAL_REPOSITORY_CLONE_FUNC


def _server_option(value: str) -> str:
    """Validate one Git protocol-v2 clone server option."""
    if "\n" in value or "\x00" in value:
        raise argparse.ArgumentTypeError(
            "server option contains an invalid NUL or LF character"
        )
    return value


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
        help="create a bandwidth-saving protocol-v2 shallow clone",
    )
    parser.add_argument(
        "--server-option",
        action="append",
        default=[],
        type=_server_option,
        metavar="OPTION",
        help="transmit an ordered server-specific option using protocol version 2",
    )
    args = parser.parse_args(list(argv))

    if args.depth is not None and args.depth <= 0:
        parser.error("--depth must be a positive integer")

    server_options = tuple(args.server_option)

    # Native Git makes --depth imply --single-branch unless the user explicitly
    # asks for --no-single-branch.
    single_branch = (
        args.single_branch
        if args.single_branch is not None
        else args.depth is not None
    )

    # A real depth clone always uses the Phase204+ truncated protocol-v2 path.
    # Preserve the historical Repository.clone override seam only when no new
    # Phase209 transport metadata is requested.
    if args.depth is not None and (
        server_options or not _repository_clone_overridden()
    ):
        shallow_kwargs = {
            "depth": args.depth,
            "branch_name": args.branch,
            "single_branch": single_branch,
        }
        if server_options:
            shallow_kwargs["server_options"] = server_options
        repo = clone_shallow_repository(
            args.url,
            args.directory,
            **shallow_kwargs,
        )
    else:
        # Preserve the historical Repository.clone call shape whenever a caller
        # deliberately replaces that classmethod. With the real method installed,
        # ordinary server-option clones reuse the mature clone/import pipeline and
        # change only the command-scoped transport to protocol v2.
        clone_kwargs = {
            "branch_name": args.branch,
            "single_branch": single_branch,
        }
        if args.depth is not None:
            clone_kwargs["depth"] = args.depth

        if server_options:
            with protocol_v2_transport(server_options=server_options):
                repo = Repository.clone(
                    args.url,
                    args.directory,
                    **clone_kwargs,
                )
        else:
            repo = Repository.clone(
                args.url,
                args.directory,
                **clone_kwargs,
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
