"""Modern ``pygit fetch`` honoring remote fetch mappings and policy."""

from __future__ import annotations

import argparse
from typing import Sequence

from .fetch_configured import fetch_configured
from .fetch_direct import fetch_direct_url, is_direct_fetch_url
from .fetch_head import write_fetch_head
from .fetch_porcelain import fetch_porcelain
from .remote_ops import configured_upstream
from .remote_urls import fetch_url
from .tracking import find_repo


def _default_fetch_remote(repo) -> str:
    branch = repo.refs.current_branch()
    if branch:
        upstream = configured_upstream(repo, branch)
        if upstream is not None and upstream.remote != ".":
            return upstream.remote
    return "origin"


def _write_configured_fetch_head(repo, remote: str, result: dict) -> None:
    default = result.get("default_branch")
    default_ref = f"refs/heads/{default}" if default else None
    mergeable = [default_ref] if default_ref in result.get("refs", {}) else []
    write_fetch_head(
        repo.pygit_dir,
        result.get("refs", {}),
        source=fetch_url(repo, remote),
        mergeable=mergeable,
    )


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
    parser.add_argument(
        "--refmap",
        action="append",
        default=None,
        metavar="REFSPEC",
        help=(
            "map command-line fetch refs with REFSPEC instead of "
            "remote.<name>.fetch; an empty value disables configured mapping"
        ),
    )

    prune_group = parser.add_mutually_exclusive_group()
    prune_group.add_argument(
        "-p", "--prune", dest="prune", action="store_true", default=None,
        help="prune stale refs before fetching",
    )
    prune_group.add_argument(
        "--no-prune", dest="prune", action="store_false",
        help="disable configured fetch pruning",
    )

    prune_tags_group = parser.add_mutually_exclusive_group()
    prune_tags_group.add_argument(
        "-P", "--prune-tags", dest="prune_tags", action="store_true", default=None,
        help="fetch tags explicitly and prune them when pruning is enabled",
    )
    prune_tags_group.add_argument(
        "--no-prune-tags", dest="prune_tags", action="store_false",
        help="disable configured tag pruning",
    )

    tag_group = parser.add_mutually_exclusive_group()
    tag_group.add_argument(
        "-t", "--tags", dest="tags", action="store_true", default=None,
        help="fetch all remote tags",
    )
    tag_group.add_argument(
        "-n", "--no-tags", dest="tags", action="store_false",
        help="disable automatic tag following",
    )

    args = parser.parse_args(list(argv))
    repo = find_repo()
    remote = args.remote or _default_fetch_remote(repo)

    if args.refmap is not None and not args.refspecs:
        raise RuntimeError("--refmap option is only meaningful with command-line refspec(s)")

    if is_direct_fetch_url(remote):
        if args.prune is True or args.prune_tags is True:
            raise RuntimeError("pruning a direct URL fetch is not supported without a named remote")
        result = fetch_direct_url(
            repo,
            remote,
            refspecs=args.refspecs or None,
            refmap=args.refmap,
            tags=args.tags,
            append_fetch_head=args.append,
        )
    # Keep the established Phase183 `fetch_configured` seam for ordinary
    # configured fetches. Explicit refspecs, --refmap, and --append need the
    # richer Phase184/185 porcelain orchestration.
    elif not args.refspecs and not args.append:
        result = fetch_configured(
            repo,
            remote,
            prune=args.prune,
            prune_tags=args.prune_tags,
            tags=args.tags,
        )
        _write_configured_fetch_head(repo, remote, result)
    else:
        result = fetch_porcelain(
            repo,
            remote,
            prune=args.prune,
            prune_tags=args.prune_tags,
            tags=args.tags,
            refspecs=args.refspecs or None,
            refmap=args.refmap,
            append_fetch_head=args.append,
        )

    suffix = f"; pruned {len(result['pruned'])} refs" if result["pruned"] else ""
    print(f"Fetched {len(result['refs'])} refs from {remote}{suffix}")
    return 0
