"""CLI adapter for coordinated safe repository maintenance."""

from __future__ import annotations

import argparse
from typing import Optional, Sequence

from .entrypoint import _find_repo
from .gc import garbage_collect
from .prune_cli import parse_expire


def _cutoff(value: Optional[str]) -> Optional[float]:
    if value is None or value.strip().lower() == "default":
        return None
    return parse_expire(value)


def run_gc(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit gc",
        description="Run verified repack, reflog-expiry, and loose-object maintenance.",
    )
    prune_group = parser.add_mutually_exclusive_group()
    prune_group.add_argument(
        "--prune",
        nargs="?",
        const="default",
        metavar="WHEN",
        help="prune loose unreachable objects older than WHEN (default: two weeks ago)",
    )
    prune_group.add_argument(
        "--no-prune",
        action="store_true",
        help="skip unreachable loose-object pruning",
    )
    parser.add_argument(
        "--no-reflog-expire",
        action="store_true",
        help="keep all reflog records during this gc pass",
    )
    parser.add_argument(
        "--reflog-expire",
        metavar="WHEN",
        help="override the general reflog expiry cutoff (default: 90 days ago)",
    )
    parser.add_argument(
        "--reflog-expire-unreachable",
        metavar="WHEN",
        help="override unreachable reflog expiry cutoff (default: 30 days ago)",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="validate and show the maintenance plan without changing repository state",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="show maintenance details")
    args = parser.parse_args(list(argv))

    result = garbage_collect(
        _find_repo(),
        prune_expire_before=_cutoff(args.prune),
        reflog_expire_before=_cutoff(args.reflog_expire),
        reflog_unreachable_before=_cutoff(args.reflog_expire_unreachable),
        expire_reflogs_enabled=not args.no_reflog_expire,
        prune_objects=not args.no_prune,
        dry_run=args.dry_run,
    )

    if args.verbose or args.dry_run:
        verb = "would repack" if args.dry_run else "repacked"
        print(
            f"{verb} {result.repack.object_count} object(s); "
            f"reachable {result.preflight_reachable}; "
            f"redundant packs {len(result.repack.removed_packs)}"
        )
        if args.dry_run:
            print(f"would prune {len(result.repack.loose_candidates)} packed loose duplicate(s)")
        else:
            print(f"pruned {result.repack.pruned_loose} packed loose duplicate(s)")

        if result.reflog is not None:
            verb = "would expire" if args.dry_run else "expired"
            print(
                f"{verb} {result.reflog.expired} reflog record(s) "
                f"across {result.reflog.scanned_logs} log(s)"
            )
        else:
            print("reflog expiry skipped")

        if result.prune is not None:
            if args.dry_run:
                print(
                    f"would prune {len(result.prune.oids)} loose unreachable object(s) "
                    "under current reflog roots (conservative dry-run)"
                )
            else:
                print(f"pruned {result.prune.pruned} loose unreachable object(s)")
        else:
            print("unreachable-object prune skipped")

    return 0
