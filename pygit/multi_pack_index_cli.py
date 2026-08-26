"""CLI adapter for ``pygit multi-pack-index``."""

from __future__ import annotations

import argparse
from typing import Sequence

from .entrypoint import _find_repo
from .multi_pack_index import verify_multi_pack_index, write_multi_pack_index


def run_multi_pack_index(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit multi-pack-index",
        description="Write or verify the shared index for multiple pygit packfiles.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "write",
        help="write .pygit/objects/pack/multi-pack-index from current pack indexes",
    )
    subparsers.add_parser(
        "verify",
        help="verify the multi-pack-index and its source pack-index mappings",
    )
    args = parser.parse_args(list(argv))

    repo = _find_repo()
    pack_dir = repo.pygit_dir / "objects" / "pack"
    midx_path = pack_dir / "multi-pack-index"
    if args.command == "write":
        write_multi_pack_index(pack_dir)
        return 0

    verify_multi_pack_index(midx_path)
    return 0
