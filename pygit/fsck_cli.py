"""Modern CLI adapter for ``pygit fsck`` reflog and recovery semantics."""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from .entrypoint import _find_repo
from .fsck import fsck
from .fsck_diagnostics import annotated_tags, format_tag_diagnostic, root_commits
from .fsck_lost_found import write_lost_found
from .fsck_names import reachable_object_names, render_issue_with_name
from .fsck_references import verify_references


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
        help="walk only objects reachable from the selected fsck roots",
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
        "--root",
        action="store_true",
        help="report root commits found during a full object scan",
    )
    parser.add_argument(
        "--tags",
        action="store_true",
        help="report annotated tag objects and their targets",
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
        "--cache",
        action="store_true",
        help="also treat index entries as heads when explicit objects are supplied",
    )
    parser.add_argument(
        "--name-objects",
        action="store_true",
        help="decorate reachable object diagnostics with rev-parse-style names",
    )
    parser.add_argument(
        "--no-references",
        action="store_true",
        help="skip the independent reference-database consistency check",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="treat fsck warnings as a failing result",
    )
    parser.add_argument(
        "objects",
        nargs="*",
        metavar="OBJECT",
        help="objects to use as the complete reachability head set",
    )
    args = parser.parse_args(list(argv))

    repo = _find_repo()
    report = fsck(
        repo,
        connectivity_only=args.connectivity_only,
        include_reflogs=not args.no_reflogs,
        heads=args.objects,
        include_index=True if args.cache else None,
    )
    if not args.no_references:
        report.issues.extend(verify_references(repo))

    names = reachable_object_names(repo, report) if args.name_objects else {}
    for issue in sorted(
        report.issues,
        key=lambda item: (
            item.severity != "error",
            item.code,
            item.oid or "",
            item.source or "",
        ),
    ):
        if args.name_objects:
            print(render_issue_with_name(issue, names), file=sys.stderr)
        else:
            print(issue.render(), file=sys.stderr)

    recovery_failed = False
    if args.lost_found and not report.errors:
        try:
            write_lost_found(repo, sorted(report.dangling))
        except Exception as exc:
            recovery_failed = True
            print(f"error: lost-found: {exc}", file=sys.stderr)

    # Native fsck's --root/--tags are full-scan diagnostics.  In
    # --connectivity-only mode Git suppresses them rather than presenting an
    # incomplete view of the object database.
    if not args.connectivity_only:
        if args.root:
            for oid in root_commits(repo, report):
                suffix = f" ({names[oid]})" if oid in names else ""
                print(f"root {oid}{suffix}")
        if args.tags:
            for entry in annotated_tags(repo, report):
                text = format_tag_diagnostic(entry)
                suffix = f" ({names[entry.oid]})" if entry.oid in names else ""
                print(text + suffix)

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
        suffix = f" ({names[oid]})" if oid in names else ""
        print(f"{label} {kind} {oid}{suffix}")

    failed = bool(report.errors) or recovery_failed or (args.strict and bool(report.warnings))
    return 1 if failed else 0
