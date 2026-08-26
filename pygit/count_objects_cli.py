"""Modern command-line adapter for ``count-objects`` diagnostics."""

from __future__ import annotations

import argparse
from typing import Sequence

from .entrypoint import _find_repo


def _human_size(size_bytes: int) -> str:
    units = ("bytes", "KiB", "MiB", "GiB", "TiB")
    value = float(size_bytes)
    for unit in units:
        if unit == "bytes" and value < 1024:
            count = int(value)
            return f"{count} byte" if count == 1 else f"{count} bytes"
        if value < 1024 or unit == units[-1]:
            return f"{value:.2f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")


def run_count_objects(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit count-objects",
        description="Count loose objects and report object-database storage diagnostics.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="report loose, packed, pruneable, garbage, and alternate storage",
    )
    parser.add_argument(
        "-H",
        "--human-readable",
        action="store_true",
        help="print storage sizes in human-readable binary units",
    )
    args = parser.parse_args(list(argv))

    info = _find_repo().count_objects()
    if not args.verbose:
        if args.human_readable:
            print(f"{info['count']} objects, {_human_size(int(info['size_bytes']))}")
        else:
            print(f"{info['count']} objects, {info['size_kb']} kilobytes")
        return 0

    size = _human_size(int(info["size_bytes"])) if args.human_readable else str(info["size_kb"])
    size_pack = (
        _human_size(int(info["size_pack_bytes"]))
        if args.human_readable
        else str(info["size_pack_kb"])
    )
    size_garbage = (
        _human_size(int(info["size_garbage_bytes"]))
        if args.human_readable
        else str(info["size_garbage_kb"])
    )

    print(f"count: {info['count']}")
    print(f"size: {size}")
    print(f"in-pack: {info['in_pack']}")
    print(f"packs: {info['packs']}")
    print(f"size-pack: {size_pack}")
    print(f"prune-packable: {info['prune_packable']}")
    print(f"garbage: {info['garbage']}")
    print(f"size-garbage: {size_garbage}")
    for alternate in info.get("alternates", []):
        print(f"alternate: {alternate}")
    return 0
