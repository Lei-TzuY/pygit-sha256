"""CLI adapter for strict pack-index inspection."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from .pack_index import parse_index, parse_index_bytes


def _stdin_bytes() -> bytes:
    binary = getattr(sys.stdin, "buffer", None)
    if binary is not None:
        return binary.read()
    data = sys.stdin.read()
    return data if isinstance(data, bytes) else data.encode("latin1")


def run_show_index(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit show-index",
        description="Validate and display a pygit SHA-256 pack index.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="include each entry's CRC32 after its object ID",
    )
    parser.add_argument(
        "--count",
        action="store_true",
        help="print only the number of indexed objects",
    )
    parser.add_argument(
        "file",
        nargs="?",
        metavar="FILE",
        help="index file to inspect; read raw index bytes from stdin when omitted",
    )
    args = parser.parse_args(list(argv))

    index = parse_index(Path(args.file)) if args.file else parse_index_bytes(_stdin_bytes())
    if args.count:
        print(index.object_count)
        return 0

    for entry in index.entries:
        if args.verbose:
            print(f"{entry.offset} {entry.oid} {entry.crc32:08x}")
        else:
            print(f"{entry.offset} {entry.oid}")
    return 0
