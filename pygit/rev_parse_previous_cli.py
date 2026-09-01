"""Focused ``rev-parse`` support for Git previous-checkout selectors."""

from __future__ import annotations

import argparse
from typing import Sequence

from .branch_checkout import expand_previous_checkout
from .entrypoint import _find_repo
from .revision import resolve_revision, short_refname, symbolic_refname


def run_rev_parse_previous(argv: Sequence[str]) -> int:
    """Resolve one ``@{-N}`` selector with ref-aware output modes.

    The ordinary ``rev-parse`` implementation remains responsible for all other
    revision grammar.  This adapter exists because ``@{-N}`` is not an object
    name by itself: Git first expands it from checkout history and only then
    resolves the selected branch/commit.
    """

    parser = argparse.ArgumentParser(
        prog="pygit rev-parse",
        description="Resolve a previous checkout revision.",
    )
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--abbrev-ref", action="store_true")
    output.add_argument("--symbolic-full-name", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("-q", "--quiet", action="store_true")
    parser.add_argument("revision", metavar="@{-N}")
    args = parser.parse_args(list(argv))

    repo = _find_repo()
    try:
        expanded = expand_previous_checkout(repo, args.revision)
        if expanded is None:
            raise ValueError(f"{args.revision!r} is not a previous checkout selector")
        oid = resolve_revision(repo, expanded)
    except (KeyError, ValueError, RuntimeError):
        if args.quiet:
            return 1
        raise

    refname = symbolic_refname(repo, expanded)
    if args.symbolic_full_name:
        if refname is not None:
            print(refname)
    elif args.abbrev_ref:
        if refname is not None:
            print(short_refname(refname))
    else:
        print(oid)
    return 0
