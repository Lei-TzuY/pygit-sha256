"""Modern ``pygit checkout`` with Git-style branch tracking setup."""

from __future__ import annotations

import argparse
from typing import Sequence

from .tracking import (
    choose_remote_candidate,
    configure_new_branch_tracking,
    find_repo,
    remote_tracking_source,
    set_branch_upstream,
)


def _checkout_created_branch(repo, branch: str, start_point: str, *, track, no_track) -> None:
    repo.branch(branch, start_point=start_point)
    configure_new_branch_tracking(
        repo,
        branch,
        start_point,
        track=track,
        no_track=no_track,
    )
    repo.checkout(branch)
    print(f"Switched to a new branch '{branch}'")


def run_checkout(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit checkout",
        description="Switch branches or restore the working tree.",
    )
    parser.add_argument("-b", dest="create", action="store_true", help="create and switch to a branch")
    track_group = parser.add_mutually_exclusive_group()
    track_group.add_argument(
        "-t",
        dest="track",
        action="store_const",
        const="direct",
        help="set direct upstream tracking for a newly created branch",
    )
    track_group.add_argument(
        "--track",
        dest="track",
        choices=("direct", "inherit"),
        metavar="{direct,inherit}",
        help="set upstream tracking for a newly created branch",
    )
    track_group.add_argument(
        "--no-track",
        action="store_true",
        help="do not configure upstream tracking for a new branch",
    )
    parser.add_argument("--detach", action="store_true", help="detach HEAD at the named commit")
    parser.add_argument("--orphan", action="store_true", help="create a new orphan branch")
    parser.add_argument("-p", "--patch", action="store_true", help="interactively restore patch hunks")
    parser.add_argument("target", nargs="?", metavar="BRANCH|SHA")
    parser.add_argument("start_point", nargs="?", metavar="START_POINT")
    parser.add_argument("paths", nargs="*", metavar="PATH")
    normalized = ["--track=direct" if token == "--track" else token for token in argv]
    args = parser.parse_args(normalized)

    repo = find_repo()

    if args.patch and args.paths:
        for path in args.paths:
            repo.apply_hunk_to_worktree(path)
            print(f"Restored patch hunk for '{path}'")
        return 0

    if args.orphan:
        if not args.target:
            parser.error("branch name required for --orphan")
        repo.checkout(args.target, orphan=True)
        print(f"Switched to a new orphan branch '{args.target}'")
        return 0

    if args.paths:
        target = (
            args.target
            if args.target
            and not repo.refs.resolve(args.target)
            and not repo.store.resolve_prefix(args.target)
            else "HEAD"
        )
        if target != "HEAD":
            restored = repo.checkout_paths(args.paths, target=target)
        else:
            all_paths = ([args.target] if args.target else []) + args.paths
            restored = repo.checkout_paths(all_paths, target="HEAD")
        for path in restored:
            print(f"Updated {path}")
        return 0

    if args.create:
        if not args.target:
            parser.error("branch name required with -b")
        start_point = args.start_point or "HEAD"
        _checkout_created_branch(
            repo,
            args.target,
            start_point,
            track=args.track,
            no_track=args.no_track,
        )
        return 0

    if args.track is not None:
        if args.track != "direct":
            raise RuntimeError("--track=inherit requires -b and a local branch start-point")
        if not args.target:
            parser.error("--track requires a remote-tracking branch")
        source = remote_tracking_source(repo, args.target)
        if source is None:
            raise RuntimeError(
                f"--track requires a remote-tracking branch, got '{args.target}'"
            )
        local_branch = source.branch
        if repo.refs.get_branch(local_branch):
            raise RuntimeError(f"a branch named '{local_branch}' already exists")
        repo.branch(local_branch, start_point=source.display)
        set_branch_upstream(repo, local_branch, source)
        repo.checkout(local_branch)
        print(f"Switched to a new branch '{local_branch}'")
        return 0

    if args.no_track:
        raise RuntimeError("--no-track is only meaningful when creating a branch")

    if not args.target:
        raise RuntimeError("checkout requires a branch, commit, or path")

    if args.detach:
        sha = repo.refs.resolve(args.target)
        if not sha:
            raise KeyError(f"Unknown revision: '{args.target}'")
        repo.checkout(args.target)
        repo.refs.set_head_detached(sha, message=f"checkout: moving to {args.target}")
        print(f"HEAD is now at {sha[:12]}")
        return 0

    if repo.refs.get_branch(args.target):
        repo.checkout(args.target)
        print(f"Switched to branch '{args.target}'")
        return 0

    # Git's default checkout guess: a missing local branch may be created from
    # one same-named remote-tracking branch. checkout.defaultRemote resolves
    # the otherwise ambiguous multi-remote case.
    if "/" not in args.target and not repo.refs.resolve(args.target):
        candidate = choose_remote_candidate(repo, args.target)
        if candidate is not None:
            repo.branch(args.target, start_point=candidate.display)
            set_branch_upstream(repo, args.target, candidate)
            repo.checkout(args.target)
            print(f"Switched to a new branch '{args.target}'")
            return 0

    sha = repo.refs.resolve(args.target)
    if not sha:
        raise KeyError(f"Unknown revision: '{args.target}'")
    repo.checkout(args.target)
    if repo.refs.get_branch(args.target):
        print(f"Switched to branch '{args.target}'")
    else:
        print(f"HEAD is now at {sha[:12]}")
    return 0
