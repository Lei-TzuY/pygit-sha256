"""CLI handlers for low-level pack import plumbing."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from .entrypoint import _find_repo
from .pack_plumbing import index_pack, unpack_objects


def run_index_pack(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit index-pack",
        description="Validate a pygit SHA-256 pack and create its fan-out index.",
    )
    parser.add_argument("-f", "--force", action="store_true", help="replace an existing sibling .idx file")
    parser.add_argument("--verbose", action="store_true", help="print indexed object IDs and metadata")
    parser.add_argument("pack", metavar="PACK")
    args = parser.parse_args(list(argv))

    result = index_pack(Path(args.pack), force=args.force)
    if args.verbose:
        from .pack_plumbing import parse_pack

        parsed = parse_pack(result.pack_path)
        for entry in parsed.entries:
            print(
                f"{entry.oid} {entry.type_name} {entry.size} "
                f"{entry.compressed_size} {entry.offset}"
            )
    print(result.idx_path)
    return 0


def run_unpack_objects(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit unpack-objects",
        description="Validate a pygit SHA-256 pack and materialize loose objects.",
    )
    parser.add_argument("-n", "--dry-run", action="store_true", help="validate only; do not write loose objects")
    parser.add_argument("--strict", action="store_true", help="run fsck connectivity checks after unpacking")
    parser.add_argument("pack", metavar="PACK")
    args = parser.parse_args(list(argv))

    repo = _find_repo()
    result = unpack_objects(repo, Path(args.pack), dry_run=args.dry_run)
    if args.strict and not args.dry_run:
        from .fsck import fsck

        report = fsck(repo)
        if report.errors:
            for issue in report.errors:
                print(issue.render())
            return 1
    print(
        f"objects {result.object_count}; written {result.written}; "
        f"existing {result.existing}"
    )
    return 0
