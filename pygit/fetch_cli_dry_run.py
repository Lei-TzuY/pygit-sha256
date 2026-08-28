"""Phase192 wrapper adding Git-style ``fetch --dry-run`` semantics."""

from __future__ import annotations

from typing import Sequence

from .fetch_cli import run_fetch as _run_fetch
from .fetch_dry_run import dry_run_repository
from .tracking import find_repo


def _dry_run_requested(argv: Sequence[str]) -> bool:
    """Recognize the option only before ``--``; later tokens are refspecs."""
    for arg in argv:
        if arg == "--":
            return False
        if arg == "--dry-run":
            return True
    return False


def _without_fetch_head_writes(argv: Sequence[str]) -> list[str]:
    forwarded: list[str] = []
    options = True
    for arg in argv:
        if options and arg == "--":
            options = False
            forwarded.append(arg)
            continue
        if options and arg in {"--dry-run", "--write-fetch-head", "--no-write-fetch-head"}:
            continue
        forwarded.append(arg)
    forwarded.append("--no-write-fetch-head")
    return forwarded


def run_fetch(argv: Sequence[str]) -> int:
    """Run fetch, sandboxing repository mutations under ``--dry-run``."""
    args = list(argv)
    if not _dry_run_requested(args):
        return _run_fetch(args)

    repo = find_repo()
    with dry_run_repository(repo):
        return _run_fetch(_without_fetch_head_writes(args))
