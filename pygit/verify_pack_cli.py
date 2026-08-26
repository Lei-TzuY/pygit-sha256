"""Command-line adapter for strict pack/index verification."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from .verify_pack import verify_pack


def run_verify_pack(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit verify-pack",
        description="Validate paired pygit pack index and archive files.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="show each verified object and the non-delta summary",
    )
    parser.add_argument("idx", nargs="+", metavar="PACK.idx")
    args = parser.parse_args(list(argv))

    failed = False
    for raw_path in args.idx:
        path = Path(raw_path)
        try:
            result = verify_pack(path)
        except (OSError, ValueError, RuntimeError, KeyError) as exc:
            print(f"{path}: bad", file=sys.stderr)
            print(f"error: {exc}", file=sys.stderr)
            failed = True
            continue

        if args.verbose:
            for obj in result.objects:
                print(
                    f"{obj.oid} {obj.type_name} {obj.size} "
                    f"{obj.packed_size} {obj.offset}"
                )
            print(f"non delta: {result.object_count} objects")
        print(f"{path}: ok")

    return 1 if failed else 0
