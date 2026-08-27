"""Modern ``pygit push`` with Git-style remote and refspec selection."""

from __future__ import annotations

import argparse
from typing import Sequence

from .push_defaults import PushPlan, all_branch_specs, all_tag_specs, delete_specs, resolve_push_plan
from .push_follow_tags import follow_tag_specs, resolve_follow_tags
from .push_includes import extract_force_if_includes, resolve_force_if_includes
from .push_lease import extract_force_with_lease
from .push_options import resolve_push_options
from .push_prune import prune_specs
from .push_transport import delete_remote_ref, push_atomic_specs, push_branch, push_ref
from .remote_ops import resolve_push_remote
from .tracking import TrackingSource, find_repo, set_branch_upstream


def run_push(argv: Sequence[str]) -> int:
    lease_argv, lease = extract_force_with_lease(argv)
    cleaned_argv, includes_override = extract_force_if_includes(lease_argv)
    parser = argparse.ArgumentParser(
        prog="pygit push",
        description="Update remote refs using Git-style push defaults and refspecs.",
    )
    parser.add_argument("repository", nargs="?", metavar="REPOSITORY")
    parser.add_argument("refspecs", nargs="*", metavar="REFSPEC")
    parser.add_argument("-f", "--force", action="store_true", help="force non-fast-forward updates")
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
    parser.add_argument(
        "--force-if-includes",
        action="store_true",
        help="require the leased remote-tracking tip to appear in local reflog history",
    )
    parser.add_argument(
        "--no-force-if-includes",
        action="store_true",
        help="disable force-if-includes even when configured",
    )
    parser.add_argument(
        "-o",
        "--push-option",
        dest="push_options",
        action="append",
        default=None,
        metavar="OPTION",
        help="transmit an option string to receive-pack hooks (repeatable)",
    )
    parser.add_argument("--all", "--branches", dest="all_branches", action="store_true", help="push all local branches")
    parser.add_argument("--tags", action="store_true", help="push all local tags")
    follow_tags = parser.add_mutually_exclusive_group()
    follow_tags.add_argument(
        "--follow-tags",
        dest="follow_tags",
        action="store_true",
        help="push missing annotated tags reachable from refs being pushed",
    )
    follow_tags.add_argument(
        "--no-follow-tags",
        dest="follow_tags",
        action="store_false",
        help="do not automatically push reachable annotated tags",
    )
    parser.add_argument("--prune", action="store_true", help="remove remote refs missing a local counterpart under selected patterns")
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
    parser.set_defaults(atomic=False, follow_tags=None)
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
    if args.delete and args.prune:
        parser.error("--delete cannot be combined with --prune")
    if args.delete and not args.refspecs:
        parser.error("--delete requires at least one ref name")

    repo = find_repo()
    push_options = resolve_push_options(repo, args.push_options)
    remote = resolve_push_remote(repo, args.repository)
    branch = repo.refs.current_branch()
    lease = lease.with_force_if_includes(
        resolve_force_if_includes(repo, includes_override)
    )
    follow_tags_enabled = resolve_follow_tags(repo, args.follow_tags)

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

    if args.prune:
        deletions = prune_specs(
            repo,
            remote,
            plan,
            args.refspecs,
            all_branches=args.all_branches,
            tags=args.tags,
        )
        if deletions:
            plan = PushPlan(
                plan.remote,
                tuple(plan.specs) + deletions,
                plan.mode,
                auto_setup_upstream=plan.auto_setup_upstream,
            )

    if follow_tags_enabled:
        additions = follow_tag_specs(repo, remote, plan, args.refspecs)
        if additions:
            plan = PushPlan(
                plan.remote,
                tuple(plan.specs) + additions,
                plan.mode,
                auto_setup_upstream=plan.auto_setup_upstream,
            )

    if not plan.specs:
        print("Everything up-to-date")
        return 0

    effective_lease = lease if lease.active and not args.force else None

    if args.atomic:
        atomic_kwargs = {"force": args.force}
        if effective_lease is not None:
            atomic_kwargs["lease"] = effective_lease
        if push_options:
            atomic_kwargs["push_options"] = push_options
        results = push_atomic_specs(repo, remote, plan.specs, **atomic_kwargs)
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
            and not push_options
        )
        for spec in plan.specs:
            effective_force = bool(args.force or spec.force)
            spec_lease = None if effective_force else effective_lease
            transport_kwargs = {"force": effective_force}
            if spec_lease is not None:
                transport_kwargs["lease"] = spec_lease
            if push_options:
                transport_kwargs["push_options"] = push_options

            if spec.delete:
                result = delete_remote_ref(
                    repo,
                    remote,
                    spec.target_ref,
                    **transport_kwargs,
                )
            elif legacy_single:
                result = repo.push(remote, force=effective_force)
            elif spec.namespace == "heads":
                result = push_branch(
                    repo,
                    remote,
                    spec.source,
                    spec.target,
                    **transport_kwargs,
                )
            else:
                result = push_ref(
                    repo,
                    remote,
                    spec.source_ref,
                    spec.target_ref,
                    **transport_kwargs,
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
