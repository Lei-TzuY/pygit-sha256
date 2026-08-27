"""Modern CLI adapter for ``pygit ls-files``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from .index_plumbing import ls_files
from .ls_files_others import other_files
from .repo import Repository


def _find_repo() -> Repository:
    for candidate in [Path.cwd(), *Path.cwd().parents]:
        if (candidate / ".pygit").is_dir():
            return Repository(str(candidate))
    raise RuntimeError(
        "fatal: not a pygit repository "
        "(or any of the parent directories): .pygit"
    )


def run_ls_files(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit ls-files",
        description="Show information about files in the index and working tree.",
    )
    parser.add_argument("-c", "--cached", action="store_true", help="show cached paths")
    parser.add_argument(
        "-s",
        "--stage",
        action="store_true",
        help="show mode, object, stage, and path",
    )
    parser.add_argument(
        "-u",
        "--unmerged",
        action="store_true",
        help="show only unmerged index stages",
    )
    parser.add_argument(
        "-d",
        "--deleted",
        action="store_true",
        help="show tracked paths deleted from the worktree",
    )
    parser.add_argument(
        "-m",
        "--modified",
        action="store_true",
        help="show tracked paths modified in the worktree",
    )
    parser.add_argument(
        "-o",
        "--others",
        action="store_true",
        help="show untracked worktree paths",
    )
    parser.add_argument(
        "-i",
        "--ignored",
        action="store_true",
        help="show only ignored untracked paths (requires --others --exclude-standard)",
    )
    parser.add_argument(
        "--exclude-standard",
        action="store_true",
        help="apply .gitignore, .pygitignore, and .pygit/info/exclude rules",
    )
    parser.add_argument(
        "--error-unmatch",
        action="store_true",
        help="fail if any supplied path pattern matches no index entry",
    )
    parser.add_argument("-z", action="store_true", help="terminate records with NUL")
    parser.add_argument("path", nargs="*", metavar="PATH")
    args = parser.parse_args(list(argv))

    if args.ignored and not args.others:
        parser.error("--ignored currently requires --others")
    if args.ignored and not args.exclude_standard:
        parser.error("--ignored requires --exclude-standard")
    if args.exclude_standard and not args.others:
        parser.error("--exclude-standard currently applies to --others")
    if args.error_unmatch and args.others and not any(
        (args.cached, args.stage, args.unmerged, args.deleted, args.modified)
    ):
        parser.error("--error-unmatch applies to index selectors, not --others-only mode")

    repo = _find_repo()
    lines = []
    index_selector_requested = any(
        (args.cached, args.stage, args.unmerged, args.deleted, args.modified)
    )
    if index_selector_requested or not args.others:
        lines.extend(
            ls_files(
                repo,
                cached=args.cached,
                stage=args.stage,
                unmerged=args.unmerged,
                deleted=args.deleted,
                modified=args.modified,
                patterns=args.path,
                error_unmatch=args.error_unmatch,
            )
        )
    if args.others:
        lines.extend(
            other_files(
                repo,
                ignored=args.ignored,
                exclude_standard=args.exclude_standard,
                patterns=args.path,
            )
        )

    # Preserve index-plumbing order (notably stage 1/2/3 ordering) while
    # de-duplicating combined selectors. ``other_files`` is already sorted.
    lines = list(dict.fromkeys(lines))
    if lines:
        separator = "\x00" if args.z else "\n"
        sys.stdout.write(separator.join(lines) + separator)
    return 0
