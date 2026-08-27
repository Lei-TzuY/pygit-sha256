"""Modern ``pygit push`` with Git-style remote and refspec defaults."""

from __future__ import annotations

import argparse
from typing import Sequence

from .push_defaults import resolve_push_plan
from .push_transport import push_branch
from .remote_ops import resolve_push_remote
from .tracking import TrackingSource, find_repo, set_branch_upstream


def run_push(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit push",
        description="Update remote branches using Git-style push defaults.",
    )
    parser.add_argument("repository", nargs="?", metavar="REPOSITORY")
    parser.add_argument("refspecs", nargs="*", metavar="REFSPEC")
    parser.add_argument("-f", "--force", action="store_true", help="force non-fast-forward updates")
    parser.add_argument(
        "-u",
        "--set-upstream",
        action="store_true",
        help="set the current branch's upstream after a successful push",
    )
    args = parser.parse_args(list(argv))

    repo = find_repo()
    branch = repo.refs.current_branch()
    if not branch:
        raise RuntimeError("cannot push from detached HEAD")

    remote = resolve_push_remote(repo, args.repository)
    plan = resolve_push_plan(repo, remote, args.refspecs)

    if not plan.specs:
        print("Everything up-to-date")
        return 0

    results = []
    legacy_single = (
        len(plan.specs) == 1
        and plan.specs[0].source == branch
        and plan.specs[0].target == branch
    )
    for spec in plan.specs:
        effective_force = bool(args.force or spec.force)
        if legacy_single:
            # Keep the historical transport/API path for the common same-name
            # current-branch push. Phase165 callers and monkeypatches therefore
            # remain source-compatible.
            result = repo.push(remote, force=effective_force)
        else:
            result = push_branch(
                repo,
                remote,
                spec.source,
                spec.target,
                force=effective_force,
            )
        results.append((spec, result))

    if args.set_upstream or plan.auto_setup_upstream:
        current_specs = [item for item in results if item[0].source == branch]
        if len(current_specs) != 1:
            raise RuntimeError(
                "cannot set one current-branch upstream from a multi-branch push"
            )
        spec, result = current_specs[0]
        oid = str(result.get("sha") or repo.refs.get_branch(branch) or "")
        if not oid:
            raise RuntimeError("cannot set upstream without a branch tip")
        set_branch_upstream(repo, branch, TrackingSource(remote, spec.target, oid))

    for spec, result in results:
        source_note = "" if spec.source == spec.target else f"{spec.source} -> "
        print(
            f"Push result: {result['status']} {source_note}"
            f"{result['remote']}/{result['branch']} ({result['objects']} objects)"
        )
    return 0
