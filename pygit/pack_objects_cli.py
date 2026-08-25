"""CLI handler for ``pygit pack-objects``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from .entrypoint import _find_repo
from .pack_objects import pack_objects


def run_pack_objects(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit pack-objects",
        description="Create a pygit SHA-256 pack from objects or revisions read on stdin.",
    )
    parser.add_argument("--revs", action="store_true", help="walk object reachability from stdin revisions; ^REV excludes history")
    parser.add_argument("--all", action="store_true", dest="all_refs", help="include every local ref and HEAD recursively")
    parser.add_argument("--stdout", action="store_true", help="write only the binary .pack stream to stdout")
    parser.add_argument("base_name", nargs="?", metavar="BASE-NAME")
    args = parser.parse_args(list(argv))

    if args.stdout and args.base_name is not None:
        parser.error("--stdout does not accept BASE-NAME")
    if not args.stdout and args.base_name is None:
        parser.error("BASE-NAME is required unless --stdout is used")

    expressions = [line.strip() for line in sys.stdin if line.strip()]
    result = pack_objects(
        _find_repo(),
        expressions,
        revs=args.revs,
        all_refs=args.all_refs,
        output_prefix=None if args.stdout else Path(args.base_name),
        stdout=args.stdout,
    )

    if args.stdout:
        assert result.pack_data is not None
        output = getattr(sys.stdout, "buffer", None)
        if output is None:
            raise RuntimeError("binary stdout is unavailable")
        output.write(result.pack_data)
        return 0

    print(result.pack_hash)
    return 0
