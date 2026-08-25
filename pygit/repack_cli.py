"""CLI handler for ``pygit repack``."""

from __future__ import annotations

import argparse
from typing import Sequence

from .entrypoint import _find_repo
from .repack import repack


def run_repack(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit repack",
        description="Create verified SHA-256 packs and optionally remove redundant storage.",
    )
    parser.add_argument(
        "-a",
        "--all",
        action="store_true",
        dest="all_objects",
        help="repack the complete reachable object closure, including already packed objects",
    )
    parser.add_argument(
        "-d",
        "--delete-redundant",
        action="store_true",
        help="remove fully subsumed old pack pairs and redundant loose copies",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="show the maintenance plan without writing or deleting repository storage",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="show selected and cleanup details")
    args = parser.parse_args(list(argv))

    result = repack(
        _find_repo(),
        all_objects=args.all_objects,
        delete_redundant=args.delete_redundant,
        dry_run=args.dry_run,
    )

    if result.pack_hash is not None:
        print(result.pack_hash)

    if args.verbose:
        action = "would pack" if args.dry_run else "packed"
        print(f"{action} {result.object_count} object(s); reachable {result.reachable}; already packed {result.already_packed}")
        for oid in result.selected_oids:
            print(f"{action} {oid}")
        remove_action = "would remove" if args.dry_run else "removed"
        for name in result.removed_packs:
            print(f"{remove_action} {name}")
        prune_action = "would prune" if args.dry_run else "pruned"
        if args.dry_run:
            for oid in result.loose_candidates:
                print(f"{prune_action} {oid}")
        elif result.pruned_loose:
            print(f"pruned {result.pruned_loose} loose object(s)")

    return 0
