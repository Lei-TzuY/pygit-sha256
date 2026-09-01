"""Focused CLI adapter for branch creation from previous-checkout history."""

from __future__ import annotations

import argparse
from typing import Sequence

from .branch_checkout import expand_previous_checkout
from .entrypoint import _find_repo


def run_branch_previous(argv: Sequence[str]) -> int:
    """Handle ``branch <branch> @{-N}`` exactly.

    Git accepts previous-checkout selectors as ordinary revision start points for
    branch creation.  A literal ``-`` is deliberately not treated as shorthand
    here: native ``git branch <name> -`` rejects ``-`` as an invalid object name.
    """

    parser = argparse.ArgumentParser(
        prog="pygit branch",
        description="Create a branch from a previous checkout destination.",
    )
    parser.add_argument("branch", metavar="BRANCH")
    parser.add_argument("start_point", metavar="@{-N}")
    args = parser.parse_args(list(argv))

    repo = _find_repo()
    expanded = expand_previous_checkout(repo, args.start_point)
    if expanded is None:
        raise ValueError(f"{args.start_point!r} is not a previous checkout selector")

    repo.branch(args.branch, start_point=expanded)
    print(f"Created branch '{args.branch}'.")
    return 0
