"""Stable top-level application entrypoint.

Most commands continue through :mod:`pygit.launcher`.  Reflog commands are
handled here because their nested grammar must be recognized before the legacy
argparse stack; the show output remains compatible with the previous handler.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from .entrypoint import _find_repo
from .launcher import main as launcher_main
from .reflog_expire_cli import run_reflog_expire


def _run_reflog_show(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit reflog",
        description="Show recorded ref movements.",
    )
    parser.add_argument("ref", nargs="?", default="HEAD", metavar="REF")
    args = parser.parse_args(list(argv))
    for index, entry in enumerate(_find_repo().reflog(args.ref)):
        print(f"{entry.new_sha[:12]} {args.ref}@{{{index}}}: {entry.message}")
    return 0


def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] == "reflog":
        try:
            if len(argv) >= 2 and argv[1] == "expire":
                code = run_reflog_expire(argv[2:])
            else:
                code = _run_reflog_show(argv[1:])
        except (
            RuntimeError,
            ValueError,
            KeyError,
            FileNotFoundError,
            FileExistsError,
            IsADirectoryError,
            OSError,
        ) as exc:
            print(f"error: {exc}", file=sys.stderr)
            code = 1
        if code:
            raise SystemExit(code)
        return
    launcher_main()
