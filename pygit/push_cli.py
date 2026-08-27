"""Modern ``pygit push`` with Git-style remote and refspec selection."""

from __future__ import annotations

import argparse
from typing import Sequence

from .push_defaults import PushPlan, all_branch_specs, all_tag_specs, delete_specs, resolve_push_plan
from .push_lease import extract_force_with_lease
from .push_transport import delete_remote_ref, push_atomic_specs, push_branch, push_ref
from .remote_ops import resolve_push_remote
from .tracking import TrackingSource, find_repo, set_branch_upstream


def run_push(argv: Sequence[str]) -> int:
    cleaned_argv, lease = extract_force_with_lease(argv)
    parser = argparse.ArgumentParser(
        prog="pygit push",
        description="Update remote refs using Git-style push defaults and refspecs.",
    )
    parser.add_argument("repository", nargs="?", metavar="REPOSITORY")
    parser.add_argument("refspecs", nargs="*", metavar="REFSPEC")
    parser.add_argument("-f", "--force", action="store_true", help="force non-fast-forward updates")
    # These declarations are retained for --help.  The actual lease options are
    # pre-parsed so a bare --force-with-lease never consumes the following
    # repository name as an optional argument.
    parser.add_argument(
        "--force-with-lease",
        nargs="?",
        metavar="REF[:EXPECT]",
        help="force only when the remote ref still has the expected value",
    )
    parser.add_argument(
        "--no-force-with-lease",
        action="store_true",
        help="cancel preceding force-with-lease requests",
    )
    parser.add_argument("--all", "--branches", dest="all_branches", action="store_true", help="push all local branches")
    parser.add_argument("--tags", action="store_true", help="push all local tags")
    parser.add_argument("-d", "--delete", action="store_true", help="delete the listed remote refs")
    atomic = parser.add_mutually_exclusive_group()
    atomic.add_argument(
        "--atomic",
        dest="atomic",
        action="store_true",
        help="request an all-or-nothing remote ref transaction",
    )
    atomic.add_argument(
        "--no-atomic",
        dest="atomic",
        action="store_false",
        help="do not request an atomic remote ref transaction",
    )
    parser.set_defaults(atomic=False)
    parser.add_argument(
        "-u",
        "--set-upstream",
        action="store_true",
        help="set the current branch's upstream after a successful push",
    )
    args = parser.parse_args(list(cleaned_argv))

    if args.all_branches and args.refspecs:
        parser.error("--all cannot be combined with explicit refspecs")
    if args.all_branches and args.delete:
        parser.error("--all cannot be combined with --delete")
    if args.delete and args.tags:
        parser.error("--delete cannot be combined with --tags")
    if args.delete and not args.refspecs:
        parser.error("--delete requires at least one ref name")

    repo = find_repo()
    remote = resolve_push_remote(repo, args.repository)
    branch = repo.refs.current_branch()

    if args.delete:
        plan = PushPlan(remote, delete_specs(repo, args.refspecs), "delete")
    elif args.all_branches:
        specs = list(all_branch_specs(repo, force=args.force))
        if args.tags:
            specs.extend(all_tag_specs(repo, force=args.force))
        plan = PushPlan(remote, tuple(specs), "all")
    elif args.tags:
        specs = []
        if args.refspecs:
            specs.extend(resolve_push_plan(repo, remote, args.refspecs).specs)
        specs.extend(all_tag_specs(repo, force=args.force))
        plan = PushPlan(remote, tuple(specs), "tags")
    else:
        plan = resolve_push_plan(repo, remote, args.refspecs)

    if not plan.specs:
        print("Everything up-to-date")
        return 0

    effective_lease = lease if lease.active and not args.force else None

    if args.atomic:
        if effective_lease is None:
            results = push_atomic_specs(repo, remote, plan.specs, force=args.force)
        else:
            results = push_atomic_specs(
                repo,
                remote,
                plan.specs,
                force=args.force,
                lease=effective_lease,
            )
    else:
        results = []
        single_spec_forces = bool(len(plan.specs) == 1 and plan.specs[0].force)
        legacy_single = (
            branch is not None
            and len(plan.specs) == 1
            and plan.specs[0].namespace == "heads"
            and not plan.specs[0].delete
            and plan.specs[0].source == branch
            and plan.specs[0].target == branch
            and (effective_lease is None or single_spec_forces)
        )
        for spec in plan.specs:
            effective_force = bool(args.force or spec.force)
            spec_lease = None if effective_force else effective_lease
            if spec.delete:
                if spec_lease is None:
                    result = delete_remote_ref(
                        repo,
                        remote,
                        spec.target_ref,
                        force=effective_force,
                    )
                else:
                    result = delete_remote_ref(
                        repo,
                        remote,
                        spec.target_ref,
                        force=effective_force,
                        lease=spec_lease,
                    )
            elif legacy_single:
                result = repo.push(remote, force=effective_force)
            elif spec.namespace == "heads":
                if spec_lease is None:
                    result = push_branch(
                        repo,
                        remote,
                        spec.source,
                        spec.target,
                        force=effective_force,
                    )
                else:
                    result = push_branch(
                        repo,
                        remote,
                        spec.source,
                        spec.target,
                        force=effective_force,
                        lease=spec_lease,
                    )
            else:
                if spec_lease is None:
                    result = push_ref(
                        repo,
                        remote,
                        spec.source_ref,
                        spec.target_ref,
                        force=effective_force,
                    )
                else:
                    result = push_ref(
                        repo,
                        remote,
                        spec.source_ref,
                        spec.target_ref,
                        force=effective_force,
                        lease=spec_lease,
                    )
            results.append((spec, result))

    if args.set_upstream or plan.auto_setup_upstream:
        if not branch:
            raise RuntimeError("cannot set upstream from detached HEAD")
        current_specs = [
            item for item in results
            if item[0].namespace == "heads" and not item[0].delete and item[0].source == branch
        ]
        if len(current_specs) != 1:
            raise RuntimeError("cannot set one current-branch upstream from this push selection")
        spec, result = current_specs[0]
        oid = str(result.get("sha") or repo.refs.get_branch(branch) or "")
        if not oid:
            raise RuntimeError("cannot set upstream without a branch tip")
        set_branch_upstream(repo, branch, TrackingSource(remote, spec.target, oid))

    for spec, result in results:
        if spec.delete:
            display = spec.target if spec.namespace == "heads" else spec.target_ref
            print(f"Push result: {result['status']} {remote}/{display} ({result['objects']} objects)")
            continue
        if spec.namespace == "heads":
            source_note = "" if spec.source == spec.target else f"{spec.source} -> "
            display_target = f"{remote}/{spec.target}"
        else:
            source_note = "" if spec.source == spec.target else f"{spec.source_ref} -> "
            display_target = f"{remote}/{spec.target_ref}"
        print(f"Push result: {result['status']} {source_note}{display_target} ({result['objects']} objects)")
    return 0
