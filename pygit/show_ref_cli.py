"""Command-line adapter for :mod:`pygit.show_ref`."""

from __future__ import annotations

import argparse
import sys
from typing import List, Sequence

from .entrypoint import _find_repo
from .show_ref import format_show_refs, ref_exists, show_refs


_DEFAULT_ABBREV = 12


def _normalize_optional_lengths(argv: Sequence[str]) -> List[str]:
    """Keep GNU-style optional values attached to their long option."""
    normalized: List[str] = []
    for token in argv:
        if token == "--hash":
            normalized.append("--hash=64")
        elif token == "--abbrev":
            normalized.append(f"--abbrev={_DEFAULT_ABBREV}")
        else:
            normalized.append(token)
    return normalized


def _write_stdout(data: bytes) -> None:
    stream = getattr(sys.stdout, "buffer", None)
    if stream is not None:
        stream.write(data)
    else:
        sys.stdout.write(data.decode("utf-8"))


def run_show_ref(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit show-ref",
        description="List, verify, or test local loose and packed references.",
    )
    parser.add_argument("--head", action="store_true", help="include HEAD even with namespace filters")
    branches = parser.add_mutually_exclusive_group()
    branches.add_argument("--branches", action="store_true", help="show branch refs only")
    branches.add_argument("--heads", action="store_true", help="legacy alias for --branches")
    parser.add_argument("--tags", action="store_true", help="show tag refs only")
    parser.add_argument(
        "-d",
        "--dereference",
        action="store_true",
        help="emit peeled annotated-tag targets as ref^{} records",
    )
    parser.add_argument("-s", action="store_true", dest="hash_only", help="show only object IDs")
    parser.add_argument(
        "--hash",
        nargs="?",
        type=int,
        dest="hash_length",
        metavar="N",
        help="show only object IDs, optionally shortened with --hash=N",
    )
    parser.add_argument(
        "--abbrev",
        nargs="?",
        type=int,
        metavar="N",
        help=f"abbreviate displayed object IDs (default length: {_DEFAULT_ABBREV})",
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--verify", action="store_true", help="require exact fully-qualified refs/... names")
    modes.add_argument(
        "--exists",
        action="store_true",
        help="test whether one exact ref record exists without resolving its object",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="suppress successful output in --verify mode",
    )
    parser.add_argument("pattern", nargs="*", metavar="REF")
    args = parser.parse_args(_normalize_optional_lengths(argv))

    branches_only = args.branches or args.heads
    if args.exists:
        if len(args.pattern) != 1:
            parser.error("--exists requires exactly one reference")
        if (
            args.head
            or branches_only
            or args.tags
            or args.dereference
            or args.hash_only
            or args.hash_length is not None
            or args.abbrev is not None
            or args.quiet
        ):
            parser.error("--exists cannot be combined with listing, formatting, or quiet options")
        return 0 if ref_exists(_find_repo(), args.pattern[0]) else 2

    if args.verify:
        if not args.pattern:
            parser.error("--verify requires at least one reference")
        if args.head or branches_only or args.tags:
            parser.error("--verify cannot be combined with --head/--branches/--tags")
    elif args.quiet:
        parser.error("--quiet is supported only with --verify")

    repo = _find_repo()
    try:
        entries = show_refs(
            repo,
            include_head=args.head,
            branches=branches_only,
            tags=args.tags,
            patterns=() if args.verify else args.pattern,
            verify_refs=args.pattern if args.verify else (),
            dereference=args.dereference,
        )
    except (KeyError, ValueError):
        if args.verify and args.quiet:
            return 1
        raise

    if not entries:
        return 1
    if not args.quiet:
        _write_stdout(
            format_show_refs(
                repo,
                entries,
                hash_only=args.hash_only,
                hash_length=args.hash_length,
                abbrev=args.abbrev,
            )
        )
    return 0
