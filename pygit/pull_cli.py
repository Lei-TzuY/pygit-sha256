"""Modern ``pygit pull`` honoring configured branch upstreams."""

from __future__ import annotations

import argparse
from typing import Sequence

from .fetch_configured import fetch_configured
from .pull_unborn_transition import try_pull_unborn_upstream
from .remote_ops import resolve_pull_source
from .tracking import find_repo


def run_pull(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit pull",
        description="Fetch from and integrate with another repository or local branch.",
    )
    parser.add_argument("repository", nargs="?", metavar="REPOSITORY")
    parser.add_argument("refspec", nargs="?", metavar="REFSPEC")
    args = parser.parse_args(list(argv))

    repo = find_repo()
    source = resolve_pull_source(repo, args.repository, args.refspec)

    if source.remote == ".":
        target = source.branch
        if not repo.refs.get_branch(target):
            raise KeyError(f"local upstream branch not found: '{target}'")
    else:
        if source.remote not in repo.list_remotes():
            raise KeyError(f"Unknown remote: '{source.remote}'")

        initial = try_pull_unborn_upstream(repo, source)
        if initial is not None:
            print(f"Pull result: {initial['status']}")
            return 0

        fetch_configured(repo, source.remote)
        if not repo.refs.get_remote(source.remote, source.branch):
            raise KeyError(f"Remote branch not found: '{source.display}'")
        target = source.display

    result = repo.merge(target, message=f"Merge '{source.display}'")
    print(f"Pull result: {result['status']}")
    return 0
