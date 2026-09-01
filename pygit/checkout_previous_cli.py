"""CLI adapter for Git-style previous-checkout selectors."""

from __future__ import annotations

import argparse
from typing import Sequence

from .branch_checkout import checkout_previous
from .entrypoint import _find_repo


def run_checkout_previous(argv: Sequence[str]) -> int:
    """Handle the focused ``pygit checkout @{-N}`` compatibility path.

    The legacy checkout parser remains authoritative for every other checkout
    form.  The top-level application router calls this adapter only when the
    sole checkout target has previous-checkout selector syntax.
    """

    parser = argparse.ArgumentParser(
        prog="pygit checkout",
        description="Switch to a previous checkout selected by HEAD reflog history.",
    )
    parser.add_argument("selector", metavar="@{-N}")
    args = parser.parse_args(list(argv))

    repo = _find_repo()
    destination = checkout_previous(repo, args.selector)
    branch = repo.refs.current_branch()
    if branch is not None:
        print(f"Switched to branch '{branch}'")
    else:
        oid = repo.refs.resolve_head() or destination
        print(f"HEAD is now at {oid[:12]}")
    return 0
