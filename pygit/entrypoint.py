"""Extended command dispatcher for plumbing commands kept outside cli.py."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

from .cli import main as legacy_main
from .plumbing import is_ancestor, list_refs, merge_bases, peel_oid, verify_ref
from .repo import Repository


_EXTRA_COMMANDS = {"merge-base", "show-ref"}


def _find_repo() -> Repository:
    for candidate in [Path.cwd(), *Path.cwd().parents]:
        if (candidate / ".pygit").is_dir():
            return Repository(str(candidate))
    raise RuntimeError(
        "fatal: not a pygit repository "
        "(or any of the parent directories): .pygit"
    )


def _run_merge_base(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit merge-base",
        description="Find best common ancestors between two commits.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--all",
        action="store_true",
        help="output all merge bases instead of one",
    )
    mode.add_argument(
        "--is-ancestor",
        action="store_true",
        help="test whether the first commit is an ancestor of the second",
    )
    parser.add_argument("commit", nargs=2, metavar="COMMIT")
    args = parser.parse_args(list(argv))

    repo = _find_repo()
    left, right = args.commit
    if args.is_ancestor:
        return 0 if is_ancestor(repo, left, right) else 1

    bases = merge_bases(repo, left, right)
    if not bases:
        return 1
    for sha in bases if args.all else bases[:1]:
        print(sha)
    return 0


def _run_show_ref(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit show-ref",
        description="List references in the local repository.",
    )
    parser.add_argument("--head", action="store_true", help="include HEAD")
    parser.add_argument("--heads", action="store_true", help="show local branches only")
    parser.add_argument("--tags", action="store_true", help="show tags only")
    parser.add_argument(
        "-d",
        "--dereference",
        action="store_true",
        help="dereference annotated tags",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="require exact, fully-qualified ref names",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="suppress normal output; useful with --verify",
    )
    parser.add_argument("pattern", nargs="*", metavar="PATTERN")
    args = parser.parse_args(list(argv))

    repo = _find_repo()
    missing = False

    if args.verify:
        if not args.pattern:
            parser.error("--verify requires at least one exact ref name")
        records = []
        for refname in args.pattern:
            try:
                record = verify_ref(repo, refname)
            except KeyError:
                missing = True
                continue
            records.append(record)
    else:
        records = list_refs(
            repo,
            include_head=args.head,
            heads=args.heads,
            tags=args.tags,
            patterns=args.pattern,
        )

    if not args.quiet:
        for oid, refname in records:
            print(f"{oid} {refname}")
            if args.dereference:
                peeled = peel_oid(repo, oid)
                if peeled != oid:
                    print(f"{peeled} {refname}^{{}}")

    if missing or not records:
        return 1
    return 0


def dispatch(argv: Sequence[str]) -> Optional[int]:
    """Run an extended command, or return ``None`` for the legacy CLI."""
    if not argv or argv[0] not in _EXTRA_COMMANDS:
        return None

    try:
        if argv[0] == "merge-base":
            return _run_merge_base(argv[1:])
        return _run_show_ref(argv[1:])
    except (RuntimeError, ValueError, KeyError, FileNotFoundError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def main() -> None:
    code = dispatch(sys.argv[1:])
    if code is None:
        legacy_main()
        return
    if code:
        raise SystemExit(code)
