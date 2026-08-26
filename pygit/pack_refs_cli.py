"""Modern CLI adapter for ``pygit pack-refs`` pattern selection."""

from __future__ import annotations

import argparse
from typing import Sequence

from .entrypoint import _find_repo
from .packed_refs import pack_refs


def run_pack_refs(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit pack-refs",
        description="Pack selected loose references into .pygit/packed-refs.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="pack all direct refs below refs/, not only tags or --include matches",
    )
    parser.add_argument(
        "--no-prune",
        action="store_true",
        help="keep selected loose refs after writing packed-refs",
    )
    parser.add_argument(
        "--include",
        action="append",
        default=[],
        metavar="PATTERN",
        help="pack refs matching PATTERN; may be supplied repeatedly",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="PATTERN",
        help="exclude refs matching PATTERN; may be supplied repeatedly",
    )
    args = parser.parse_args(list(argv))

    repo = _find_repo()
    pack_refs(
        repo,
        all_refs=args.all,
        prune=not args.no_prune,
        includes=args.include,
        excludes=args.exclude,
    )
    return 0
