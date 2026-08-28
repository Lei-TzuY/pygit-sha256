"""Modern ``pygit fetch`` honoring remote fetch mappings and policy."""

from __future__ import annotations

import argparse
import sys
from contextlib import nullcontext
from typing import Sequence

from .fetch_atomic import atomic_ref_updates
from .fetch_configured import fetch_configured
from .fetch_direct import fetch_direct_url, is_direct_fetch_url
from .fetch_head import write_fetch_head
from .fetch_multiple import (
    all_fetch_remotes,
    expand_fetch_sources,
    fetch_all_by_config,
    remote_group_members,
    run_multi_fetch,
)
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


def _fetch_named(
    repo,
    remote: str,
    *,
    append: bool,
    write_fetch_head_enabled: bool,
    force: bool,
    prune,
    prune_tags,
    tags,
) -> dict:
    """Fetch one named remote and optionally write/append FETCH_HEAD."""
    if remote not in repo.list_remotes():
        raise KeyError(f"Unknown remote: '{remote}'")
    if not append:
        result = fetch_configured(
            repo,
            remote,
            force=force,
            prune=prune,
            prune_tags=prune_tags,
            tags=tags,
        )
        if write_fetch_head_enabled:
            _write_configured_fetch_head(repo, remote, result)
        return result
    return fetch_porcelain(
        repo,
        remote,
        force=force,
        prune=prune,
        prune_tags=prune_tags,
        tags=tags,
        append_fetch_head=True,
        write_fetch_head=write_fetch_head_enabled,
    )


def _run_many(repo, remotes, args) -> int:
    if not remotes:
        return 0

    def fetch_one(remote: str, aggregate_append: bool) -> None:
        print(f"Fetching {remote}")
        _fetch_named(
            repo,
            remote,
            append=args.append or aggregate_append,
            write_fetch_head_enabled=args.write_fetch_head,
            force=args.force,
            prune=args.prune,
            prune_tags=args.prune_tags,
            tags=args.tags,
        )

    results = run_multi_fetch(repo, remotes, fetch_one)
    failures = [result for result in results if not result.ok]
    for result in failures:
        print(f"error: could not fetch {result.remote}: {result.error}", file=sys.stderr)
    return 1 if failures else 0


def _atomic_scope(repo, enabled: bool):
    return atomic_ref_updates(repo) if enabled else nullcontext()


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
        "-f",
        "--force",
        action="store_true",
        help="force local ref updates that Git permits to be forced",
    )
    fetch_head_group = parser.add_mutually_exclusive_group()
    fetch_head_group.add_argument(
        "--write-fetch-head",
        dest="write_fetch_head",
        action="store_true",
        default=True,
        help="write fetched refs to FETCH_HEAD (default)",
    )
    fetch_head_group.add_argument(
        "--no-write-fetch-head",
        dest="write_fetch_head",
        action="store_false",
        help="do not write FETCH_HEAD",
    )
    parser.add_argument(
        "--atomic",
        action="store_true",
        help="update local refs atomically for a single fetch source",
    )
    parser.add_argument(
        "--multiple",
        action="store_true",
        help="fetch several named remotes or remote groups",
    )
    all_group = parser.add_mutually_exclusive_group()
    all_group.add_argument(
        "--all",
        dest="all_remotes",
        action="store_true",
        default=None,
        help="fetch every configured remote except skipFetchAll remotes",
    )
    all_group.add_argument(
        "--no-all",
        dest="all_remotes",
        action="store_false",
        help="disable fetch.all for this invocation",
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

    if args.multiple:
        if args.atomic:
            raise RuntimeError("--atomic can only be used when fetching from one remote")
        if args.refmap is not None:
            raise RuntimeError("--refmap is incompatible with --multiple")
        names = ([args.remote] if args.remote else []) + list(args.refspecs)
        if args.all_remotes is True:
            raise RuntimeError("--all and --multiple cannot be combined")
        if not names:
            raise RuntimeError("--multiple requires at least one remote or group")
        return _run_many(repo, expand_fetch_sources(repo, names), args)

    if args.all_remotes is True:
        if args.atomic:
            raise RuntimeError("--atomic can only be used when fetching from one remote")
        if args.remote is not None or args.refspecs:
            raise RuntimeError("--all does not accept a repository or refspec")
        if args.refmap is not None:
            raise RuntimeError("--refmap is incompatible with --all")
        return _run_many(repo, all_fetch_remotes(repo), args)

    if (
        args.remote is None
        and args.all_remotes is not False
        and fetch_all_by_config(repo)
    ):
        if args.atomic:
            raise RuntimeError("--atomic can only be used when fetching from one remote")
        if args.refmap is not None:
            raise RuntimeError("--refmap is incompatible with fetch.all")
        return _run_many(repo, all_fetch_remotes(repo), args)

    if args.remote is not None and not args.refspecs:
        members = remote_group_members(repo, args.remote)
        if members is not None:
            if args.atomic:
                raise RuntimeError("--atomic can only be used when fetching from one remote")
            if args.refmap is not None:
                raise RuntimeError("--refmap is incompatible with a remote group")
            return _run_many(repo, list(members), args)

    remote = args.remote or _default_fetch_remote(repo)

    if args.refmap is not None and not args.refspecs:
        raise RuntimeError("--refmap option is only meaningful with command-line refspec(s)")

    with _atomic_scope(repo, args.atomic):
        if is_direct_fetch_url(remote):
            if args.prune is True or args.prune_tags is True:
                raise RuntimeError("pruning a direct URL fetch is not supported without a named remote")
            result = fetch_direct_url(
                repo,
                remote,
                refspecs=args.refspecs or None,
                refmap=args.refmap,
                tags=args.tags,
                force=args.force,
                append_fetch_head=args.append,
                write_fetch_head=args.write_fetch_head,
            )
        elif not args.refspecs and not args.append:
            result = fetch_configured(
                repo,
                remote,
                force=args.force,
                prune=args.prune,
                prune_tags=args.prune_tags,
                tags=args.tags,
            )
            if args.write_fetch_head:
                _write_configured_fetch_head(repo, remote, result)
        else:
            result = fetch_porcelain(
                repo,
                remote,
                force=args.force,
                prune=args.prune,
                prune_tags=args.prune_tags,
                tags=args.tags,
                refspecs=args.refspecs or None,
                refmap=args.refmap,
                append_fetch_head=args.append,
                write_fetch_head=args.write_fetch_head,
            )

    suffix = f"; pruned {len(result['pruned'])} refs" if result["pruned"] else ""
    print(f"Fetched {len(result['refs'])} refs from {remote}{suffix}")
    return 0
