"""CLI adapter for safe unreachable loose-object pruning."""

from __future__ import annotations

import argparse
import re
import sys
import time
from typing import Optional, Sequence

from .entrypoint import _find_repo
from .prune import default_expire_before, prune


_RELATIVE = re.compile(r"^(\d+)\.(minutes?|hours?|days?|weeks?)\.ago$")
_SECONDS = {
    "minute": 60,
    "minutes": 60,
    "hour": 60 * 60,
    "hours": 60 * 60,
    "day": 24 * 60 * 60,
    "days": 24 * 60 * 60,
    "week": 7 * 24 * 60 * 60,
    "weeks": 7 * 24 * 60 * 60,
}


def parse_expire(value: str, *, now: Optional[float] = None) -> float:
    """Parse a small deterministic subset of Git-style expiry expressions."""
    current = time.time() if now is None else float(now)
    text = value.strip().lower()
    if text == "now":
        return current
    if text == "never":
        return float("-inf")
    if text == "default":
        return default_expire_before(current)
    if text.isdigit():
        return float(int(text))
    match = _RELATIVE.fullmatch(text)
    if match:
        count = int(match.group(1))
        return current - count * _SECONDS[match.group(2)]
    raise ValueError(
        "--expire expects now, never, default, an epoch timestamp, or N.days.ago/N.hours.ago/N.weeks.ago"
    )


def run_prune(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit prune",
        description="Remove expired unreachable loose objects while preserving recovery roots.",
    )
    parser.add_argument("-n", "--dry-run", action="store_true", help="show what would be pruned without deleting")
    parser.add_argument("-v", "--verbose", action="store_true", help="print each eligible object ID")
    parser.add_argument(
        "--expire",
        default="default",
        metavar="WHEN",
        help="expiry cutoff: default, now, never, epoch, or N.days.ago (default: two weeks ago)",
    )
    parser.add_argument(
        "head",
        nargs="*",
        metavar="HEAD",
        help="additional revision roots whose reachable objects must be retained",
    )
    args = parser.parse_args(list(argv))

    result = prune(
        _find_repo(),
        expire_before=parse_expire(args.expire),
        dry_run=args.dry_run,
        extra_heads=args.head,
    )

    if args.verbose or args.dry_run:
        for oid in result.oids:
            print(oid)
    for oid in result.skipped_loose:
        print(f"warning: keeping malformed loose object {oid}", file=sys.stderr)
    return 0
