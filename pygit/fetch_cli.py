"""Modern ``pygit fetch`` honoring remote fetch mappings and policy."""

from __future__ import annotations

import argparse
from typing import Sequence

from .fetch_configured import fetch_configured
from .remote_ops import configured_upstream
from .tracking import find_repo


def _default_fetch_remote(repo) -> str:
    branch = repo.refs.current_branch()
    if branch:
        upstream = configured_upstream(repo, branch)
        if upstream is not None and upstream.remote != ".":
            return upstream.remote
    return "origin"


def run_fetch(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit fetch",
        description="Download objects and update configured remote-tracking refs.",
    )
    parser.add_argument("remote", nargs="?", metavar="REMOTE")
    parser.add_argument("refspecs", nargs="*", metavar="REFSPEC")
    parser.add_argument(
        "-a",
        "--append",
        action="store_true",
        help="append to FETCH_HEAD instead of overwriting it",
    )

    prune_group = parser.add_mutually_exclusive_group()
    prune_group.add_argument(
        "-p",
        "--prune",
        dest="prune",
        action="store_true",
        default=None,
        help="prune stale refs before fetching",
    )
    prune_group.add_argument(
        "--no-prune",
        dest="prune",
        action="store_false",
        help="disable configured fetch pruning",
    )

    prune_tags_group = parser.add_mutually_exclusive_group()
    prune_tags_group.add_argument(
        "-P",
        "--prune-tags",
        dest="prune_tags",
        action="store_true",
        default=None,
        help="fetch tags explicitly and prune them when pruning is enabled",
    )
    prune_tags_group.add_argument(
        "--no-prune-tags",
        dest="prune_tags",
        action="store_false",
        help="disable configured tag pruning",
    )

    tag_group = parser.add_mutually_exclusive_group()
    tag_group.add_argument(
        "-t",
        "--tags",
        dest="tags",
        action="store_true",
        default=None,
        help="fetch all remote tags",
    )
    tag_group.add_argument(
        "-n",
        "--no-tags",
        dest="tags",
        action="store_false",
        help="disable automatic tag following",
    )

    args = parser.parse_args(list(argv))

    repo = find_repo()
    remote = args.remote or _default_fetch_remote(repo)
    result = fetch_configured(
        repo,
        remote,
        prune=args.prune,
        prune_tags=args.prune_tags,
        tags=args.tags,
        refspecs=args.refspecs or None,
        append_fetch_head=args.append,
    )
    suffix = f"; pruned {len(result['pruned'])} refs" if result["pruned"] else ""
    print(f"Fetched {len(result['refs'])} refs from {remote}{suffix}")
    return 0
