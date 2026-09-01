"""CLI adapter for Git-style previous-checkout selectors."""

from __future__ import annotations

import argparse
from typing import Sequence

from .branch_checkout import checkout_previous
from .entrypoint import _find_repo


def run_checkout_previous(argv: Sequence[str]) -> int:
    """Handle focused previous-checkout compatibility forms.

    ``pygit checkout -`` is Git's shorthand for ``pygit checkout @{-1}``.
    ``pygit checkout --detach @{-N}`` detaches at the selected branch/commit,
    and ``pygit checkout --detach -`` is the exact ``@{-1}`` shorthand for the
    same detached operation. The legacy checkout parser remains authoritative
    for every other checkout form. The top-level application router calls this
    adapter only for the focused previous-checkout forms it owns.
    """

    parser = argparse.ArgumentParser(
        prog="pygit checkout",
        description="Switch to a previous checkout selected by HEAD reflog history.",
    )
    parser.add_argument("--detach", action="store_true")
    parser.add_argument("selector", metavar="@{-N}|-")
    args = parser.parse_args(list(argv))

    selector = "@{-1}" if args.selector == "-" else args.selector
    repo = _find_repo()
    destination = checkout_previous(repo, selector, detach=args.detach)
    branch = repo.refs.current_branch()
    if branch is not None:
        print(f"Switched to branch '{branch}'")
    else:
        oid = repo.refs.resolve_head() or destination
        print(f"HEAD is now at {oid[:12]}")
    return 0
