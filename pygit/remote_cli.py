"""Modern ``pygit remote`` lifecycle and URL porcelain."""

from __future__ import annotations

import argparse
from typing import Sequence

from .remote_head import set_remote_head
from .remote_lifecycle import add_remote, remove_remote, rename_remote
from .remote_urls import get_remote_urls, set_remote_url
from .tracking import find_repo


def _get_url(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit remote get-url",
        description="Retrieve fetch or push URLs for a named remote.",
    )
    parser.add_argument("--push", action="store_true", help="query push URLs instead of fetch URLs")
    parser.add_argument("--all", action="store_true", dest="all_urls", help="show all URLs")
    parser.add_argument("name", metavar="NAME")
    args = parser.parse_args(list(argv))

    repo = find_repo()
    for url in get_remote_urls(repo, args.name, push=args.push, all_urls=args.all_urls):
        print(url)
    return 0


def _set_url(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit remote set-url",
        description="Change fetch or push URLs for a named remote.",
    )
    parser.add_argument("--push", action="store_true", help="manipulate push URLs instead of fetch URLs")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--add", action="store_true", help="append a new URL")
    modes.add_argument("--delete", action="store_true", help="delete every URL matching URL_REGEX")
    parser.add_argument("name", metavar="NAME")
    parser.add_argument("url", metavar="NEW_URL_OR_REGEX")
    parser.add_argument("old_url", nargs="?", metavar="OLD_URL_REGEX")
    args = parser.parse_args(list(argv))

    if args.add and args.old_url is not None:
        parser.error("--add does not accept OLD_URL_REGEX")
    if args.delete and args.old_url is not None:
        parser.error("--delete accepts exactly one URL regex")

    repo = find_repo()
    set_remote_url(
        repo,
        args.name,
        args.url,
        old_url=args.old_url,
        push=args.push,
        add=args.add,
        delete=args.delete,
    )
    return 0


def _add(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit remote add",
        description="Add a named remote and its default fetch mapping.",
    )
    parser.add_argument("name", metavar="NAME")
    parser.add_argument("url", metavar="URL")
    args = parser.parse_args(list(argv))

    add_remote(find_repo(), args.name, args.url)
    return 0


def _remove(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit remote remove",
        description="Remove a remote, its tracking refs, and related configuration.",
    )
    parser.add_argument("name", metavar="NAME")
    args = parser.parse_args(list(argv))

    remove_remote(find_repo(), args.name)
    return 0


def _rename(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit remote rename",
        description="Rename a remote, its tracking refs, and related configuration.",
    )
    parser.add_argument("old", metavar="OLD")
    parser.add_argument("new", metavar="NEW")
    args = parser.parse_args(list(argv))

    rename_remote(find_repo(), args.old, args.new)
    return 0


def _set_head(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit remote set-head",
        description="Set or delete the default branch for a named remote.",
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("-a", "--auto", action="store_true", help="query the remote and set its advertised HEAD")
    modes.add_argument("-d", "--delete", action="store_true", help="delete refs/remotes/NAME/HEAD")
    parser.add_argument("name", metavar="NAME")
    parser.add_argument("branch", nargs="?", metavar="BRANCH")
    args = parser.parse_args(list(argv))

    if args.branch is None and not args.auto and not args.delete:
        parser.error("one of --auto, --delete, or BRANCH is required")
    if args.branch is not None and (args.auto or args.delete):
        parser.error("BRANCH cannot be combined with --auto or --delete")

    selected = set_remote_head(
        find_repo(),
        args.name,
        args.branch,
        auto=args.auto,
        delete=args.delete,
    )
    if args.auto and selected is not None:
        print(f"{args.name}/HEAD set to {selected}")
    return 0


def run_remote(argv: Sequence[str]) -> int:
    if not argv:
        raise ValueError("remote porcelain requires a subcommand")
    command, rest = argv[0], argv[1:]
    if command == "get-url":
        return _get_url(rest)
    if command == "set-url":
        return _set_url(rest)
    if command == "add":
        return _add(rest)
    if command in {"remove", "rm"}:
        return _remove(rest)
    if command == "rename":
        return _rename(rest)
    if command == "set-head":
        return _set_head(rest)
    raise ValueError(f"unsupported remote subcommand: {command}")
