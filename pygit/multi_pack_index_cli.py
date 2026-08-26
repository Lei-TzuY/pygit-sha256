"""CLI adapter for ``pygit multi-pack-index``."""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from .entrypoint import _find_repo
from .multi_pack_index import verify_multi_pack_index, write_multi_pack_index
from .multi_pack_index_expire import expire_multi_pack_index
from .multi_pack_index_repack import repack_multi_pack_index
from .multi_pack_index_stdin import write_multi_pack_index_from_packs


_SIZE_SUFFIXES = {
    "k": 1024,
    "m": 1024 * 1024,
    "g": 1024 * 1024 * 1024,
}


def _parse_batch_size(value: str) -> int:
    raw = value.strip()
    if not raw:
        raise argparse.ArgumentTypeError("batch size must not be empty")
    multiplier = 1
    suffix = raw[-1].lower()
    if suffix in _SIZE_SUFFIXES:
        multiplier = _SIZE_SUFFIXES[suffix]
        raw = raw[:-1]
    try:
        number = int(raw, 10)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid batch size: {value!r}") from exc
    if number < 0:
        raise argparse.ArgumentTypeError("batch size must be non-negative")
    return number * multiplier


def run_multi_pack_index(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit multi-pack-index",
        description="Write, verify, expire, or repack the shared index for multiple pygit packfiles.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    write_parser = subparsers.add_parser(
        "write",
        help="write .pygit/objects/pack/multi-pack-index from current pack indexes",
    )
    write_parser.add_argument(
        "--preferred-pack",
        metavar="PACK",
        help="prefer this pack when duplicate object copies exist",
    )
    write_parser.add_argument(
        "--stdin-packs",
        action="store_true",
        help="read .idx basenames from stdin and index only those packs",
    )
    subparsers.add_parser(
        "verify",
        help="verify the multi-pack-index and its source pack-index mappings",
    )
    subparsers.add_parser(
        "expire",
        help="delete fully redundant tracked packs and rewrite the multi-pack-index",
    )
    repack_parser = subparsers.add_parser(
        "repack",
        help="combine MIDX-referenced objects from a batch of packfiles",
    )
    repack_parser.add_argument(
        "--batch-size",
        type=_parse_batch_size,
        default=0,
        metavar="SIZE",
        help="target expected batch size in bytes; k/m/g suffixes are accepted",
    )
    args = parser.parse_args(list(argv))

    repo = _find_repo()
    pack_dir = repo.pygit_dir / "objects" / "pack"
    midx_path = pack_dir / "multi-pack-index"
    if args.command == "write":
        if args.stdin_packs:
            result = write_multi_pack_index_from_packs(
                pack_dir,
                sys.stdin,
                preferred_pack=args.preferred_pack,
            )
            if result.ignored_preferred_pack is not None:
                print(
                    f"warning: unknown preferred pack: {result.ignored_preferred_pack!r}",
                    file=sys.stderr,
                )
        else:
            write_multi_pack_index(pack_dir, preferred_pack=args.preferred_pack)
        return 0
    if args.command == "expire":
        expire_multi_pack_index(midx_path)
        return 0
    if args.command == "repack":
        repack_multi_pack_index(repo, midx_path, batch_size=args.batch_size)
        return 0

    verify_multi_pack_index(midx_path)
    return 0
