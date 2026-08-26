"""Modern CLI adapter for ``pygit ls-files``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from .index_plumbing import ls_files
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
        "--error-unmatch",
        action="store_true",
        help="fail if any supplied path pattern matches no index entry",
    )
    parser.add_argument("-z", action="store_true", help="terminate records with NUL")
    parser.add_argument("path", nargs="*", metavar="PATH")
    args = parser.parse_args(list(argv))

    repo = _find_repo()
    lines = ls_files(
        repo,
        cached=args.cached,
        stage=args.stage,
        unmerged=args.unmerged,
        deleted=args.deleted,
        modified=args.modified,
        patterns=args.path,
        error_unmatch=args.error_unmatch,
    )
    if lines:
        separator = "\x00" if args.z else "\n"
        sys.stdout.write(separator.join(lines) + separator)
    return 0
