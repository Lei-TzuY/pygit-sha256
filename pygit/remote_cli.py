"""Modern ``pygit remote get-url`` / ``set-url`` porcelain."""

from __future__ import annotations

import argparse
from typing import Sequence

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


def run_remote(argv: Sequence[str]) -> int:
    if not argv:
        raise ValueError("remote URL porcelain requires get-url or set-url")
    command, rest = argv[0], argv[1:]
    if command == "get-url":
        return _get_url(rest)
    if command == "set-url":
        return _set_url(rest)
    raise ValueError(f"unsupported remote URL subcommand: {command}")
