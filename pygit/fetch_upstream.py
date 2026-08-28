"""Git-style ``fetch --set-upstream`` tracking configuration."""

from __future__ import annotations

import sys
from typing import Sequence

from .config import GitConfig
from .fetch_policy import parse_fetch_refspec


def _source_branches(refspecs: Sequence[str]) -> list[str]:
    branches: list[str] = []
    for raw in refspecs:
        spec = parse_fetch_refspec(raw)
        if spec.negative or "*" in spec.source:
            continue
        if spec.source.startswith("refs/heads/"):
            branches.append(spec.source)
    return branches


def set_fetch_upstream(repo, remote: str, refspecs: Sequence[str]) -> bool:
    """Set the current branch's upstream after a successful fetch.

    Native Git only installs tracking configuration when exactly one source
    branch was named on the command line. Missing or ambiguous sources are a
    successful fetch with a warning rather than a fetch failure.
    """
    branches = _source_branches(refspecs)
    if not branches:
        print(
            "warning: no source branch found;\n"
            "you need to specify exactly one branch with the --set-upstream option",
            file=sys.stderr,
        )
        return False
    if len(branches) != 1:
        print(
            "warning: multiple branches detected, incompatible with --set-upstream",
            file=sys.stderr,
        )
        return False

    local = repo.refs.current_branch()
    branch_ref = branches[0]
    branch_name = branch_ref[len("refs/heads/") :]
    if not local:
        print(
            f"warning: could not set upstream of HEAD to '{branch_name}' from "
            f"'{remote}' when it does not point to any branch.",
            file=sys.stderr,
        )
        return False

    config = GitConfig(repo.pygit_dir)
    config.set("branch", f"{local}.remote", remote)
    config.set("branch", f"{local}.merge", branch_ref)
    return True
