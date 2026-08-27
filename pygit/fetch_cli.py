"""Modern ``pygit fetch`` honoring remote fetch mappings."""

from __future__ import annotations

import argparse
from typing import Sequence

from .fetch_configured import fetch_configured
from .remote_ops import configured_upstream
from .tracking import find_repo


def _default_fetch_remote(repo) -> str:
    branch = repo.refs.current_branch()
    if branch:
        upstream = configured_upstream(repo, branch)
        if upstream is not None and upstream.remote != ".":
            return upstream.remote
    return "origin"


def run_fetch(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit fetch",
        description="Download objects and update configured remote-tracking refs.",
    )
    parser.add_argument("remote", nargs="?", metavar="REMOTE")
    args = parser.parse_args(list(argv))

    repo = find_repo()
    remote = args.remote or _default_fetch_remote(repo)
    result = fetch_configured(repo, remote)
    print(f"Fetched {len(result['refs'])} refs from {remote}")
    return 0
