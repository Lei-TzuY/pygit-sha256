"""Modern CLI adapter for ``pygit fsck`` reflog and recovery semantics."""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from .entrypoint import _find_repo
from .fsck import fsck
from .fsck_lost_found import write_lost_found


def run_fsck(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit fsck",
        description="Verify SHA-256 object storage and repository connectivity.",
    )
    scan = parser.add_mutually_exclusive_group()
    scan.add_argument(
        "--full",
        action="store_true",
        help="check all loose and packed objects (the default)",
    )
    scan.add_argument(
        "--connectivity-only",
        action="store_true",
        help="walk only objects reachable from refs, index, shallow, and reflog roots",
    )
    parser.add_argument(
        "--unreachable",
        action="store_true",
        help="print every unreachable object instead of only dangling roots",
    )
    parser.add_argument(
        "--no-dangling",
        action="store_true",
        help="suppress dangling-object output",
    )
    parser.add_argument(
        "--lost-found",
        action="store_true",
        help="write dangling objects below .pygit/lost-found for recovery",
    )
    parser.add_argument(
        "--no-reflogs",
        action="store_true",
        help="do not treat reflog entries as reachability roots",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="treat fsck warnings as a failing result",
    )
    args = parser.parse_args(list(argv))

    repo = _find_repo()
    report = fsck(
        repo,
        connectivity_only=args.connectivity_only,
        include_reflogs=not args.no_reflogs,
    )

    for issue in sorted(
        report.issues,
        key=lambda item: (
            item.severity != "error",
            item.code,
            item.oid or "",
            item.source or "",
        ),
    ):
        print(issue.render(), file=sys.stderr)

    recovery_failed = False
    if args.lost_found and not report.errors:
        try:
            write_lost_found(repo, sorted(report.dangling))
        except Exception as exc:
            recovery_failed = True
            print(f"error: lost-found: {exc}", file=sys.stderr)

    if args.unreachable:
        selected = report.unreachable
        label = "unreachable"
    elif args.no_dangling:
        selected = set()
        label = "dangling"
    else:
        selected = report.dangling
        label = "dangling"

    for oid in sorted(selected):
        try:
            kind = repo.store.read(oid).type_name.decode("ascii", "replace")
        except Exception:
            kind = "object"
        print(f"{label} {kind} {oid}")

    failed = bool(report.errors) or recovery_failed or (args.strict and bool(report.warnings))
    return 1 if failed else 0
