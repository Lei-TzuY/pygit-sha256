"""Focused CLI adapter for creating a branch from previous-checkout history."""

from __future__ import annotations

import argparse
from typing import Sequence

from .branch_checkout import expand_previous_checkout
from .entrypoint import _find_repo


def run_checkout_create_previous(argv: Sequence[str]) -> int:
    """Handle ``checkout -b <branch> (-|@{-N})`` exactly.

    Previous-checkout syntax is expanded before the existing branch-creation
    and checkout operations run. Ordinary branch/tag/SHA start points remain the
    responsibility of the legacy checkout parser.
    """

    parser = argparse.ArgumentParser(
        prog="pygit checkout",
        description="Create a branch from a previous checkout destination.",
    )
    parser.add_argument("-b", dest="branch", required=True, metavar="BRANCH")
    parser.add_argument("start_point", metavar="@{-N}|-")
    args = parser.parse_args(list(argv))

    selector = "@{-1}" if args.start_point == "-" else args.start_point
    repo = _find_repo()
    expanded = expand_previous_checkout(repo, selector)
    if expanded is None:
        raise ValueError(f"{args.start_point!r} is not a previous checkout selector")

    repo.branch(args.branch, start_point=expanded)
    repo.checkout(args.branch)
    print(f"Switched to a new branch '{args.branch}'")
    return 0
