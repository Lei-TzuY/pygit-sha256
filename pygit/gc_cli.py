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
        help="legacy no-write mode; validate and report without changing repository state",
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

    dry_run = bool(args.dry_run or args.no_prune)
    result = garbage_collect(
        _find_repo(),
        prune_expire_before=_cutoff(args.prune),
        reflog_expire_before=_cutoff(args.reflog_expire),
        reflog_unreachable_before=_cutoff(args.reflog_expire_unreachable),
        expire_reflogs_enabled=not args.no_reflog_expire,
        prune_objects=True,
        dry_run=dry_run,
    )

    repack_count = result.repack.object_count
    reflog_count = result.reflog.expired if result.reflog is not None else 0
    prune_count = (
        len(result.prune.oids)
        if result.dry_run and result.prune is not None
        else result.prune.pruned if result.prune is not None else 0
    )
    prefix = "would " if result.dry_run else ""
    print(
        f"Garbage collection: {prefix}repack {repack_count} object(s), "
        f"{prefix}expire {reflog_count} reflog record(s), "
        f"{prefix}prune {prune_count} unreachable loose object(s); "
        f"retained {result.final_reachable} reachable object(s)."
    )

    if args.verbose:
        duplicate_count = (
            len(result.repack.loose_candidates)
            if result.dry_run
            else result.repack.pruned_loose
        )
        print(f"packed loose duplicate cleanup: {duplicate_count}")

        if result.reflog is not None:
            for entry in result.reflog.entries:
                print(
                    f"reflog {entry.reason}\t{entry.ref}\t"
                    f"{entry.old_oid}\t{entry.new_oid}"
                )
        else:
            print("reflog expiry skipped")

        for oid in result.repack.selected_oids:
            print(f"repack\t{oid}")
        for name in result.repack.removed_packs:
            verb = "would-remove-pack" if result.dry_run else "removed-pack"
            print(f"{verb}\t{name}")

        if result.prune is not None:
            verb = "would-prune" if result.dry_run else "pruned"
            for oid in result.prune.oids:
                print(f"{verb}\t{oid}")

        for oid in result.preserved_expired_roots:
            print(f"preserve-expired-root\t{oid}")

    return 0
