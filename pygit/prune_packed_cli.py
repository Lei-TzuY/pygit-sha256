"""CLI adapter for safe loose-object pruning after packing."""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from .entrypoint import _find_repo
from .prune_packed import prune_packed


def run_prune_packed(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit prune-packed",
        description="Remove loose objects that have fully verified packed copies.",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="show what would be removed without changing object storage",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="print each loose object selected for pruning",
    )
    args = parser.parse_args(list(argv))

    repo = _find_repo()
    result = prune_packed(repo, dry_run=args.dry_run)

    if args.verbose or args.dry_run:
        prefix = "would prune" if args.dry_run else "pruned"
        for oid in result.oids:
            print(f"{prefix} {oid}")

    for path in result.ignored_packs:
        print(f"warning: ignored untrusted pack/index pair: {path}", file=sys.stderr)
    for oid in result.skipped_loose:
        print(f"warning: kept invalid loose object: {oid}", file=sys.stderr)

    return 0
