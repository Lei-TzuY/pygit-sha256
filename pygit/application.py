"""Stable top-level application entrypoint.

Most commands continue through :mod:`pygit.launcher`.  Commands that need a
modern nested/custom grammar are handled here before the legacy argparse stack;
reflog show output remains compatible with the previous handler.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from .entrypoint import _find_repo
from .gc_cli import run_gc
from .launcher import main as launcher_main
from .ls_tree_cli import run_ls_tree
from .reflog_expire_cli import run_reflog_expire


_ERRORS = (
    RuntimeError,
    ValueError,
    KeyError,
    FileNotFoundError,
    FileExistsError,
    IsADirectoryError,
    OSError,
)


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


def _finish(code: int) -> None:
    if code:
        raise SystemExit(code)


def _run_safe(handler, argv: Sequence[str]) -> None:
    try:
        code = handler(argv)
    except _ERRORS as exc:
        print(f"error: {exc}", file=sys.stderr)
        code = 1
    _finish(code)


def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] == "reflog":
        if len(argv) >= 2 and argv[1] == "expire":
            _run_safe(run_reflog_expire, argv[2:])
        else:
            _run_safe(_run_reflog_show, argv[1:])
        return

    if argv and argv[0] == "gc":
        _run_safe(run_gc, argv[1:])
        return

    if argv and argv[0] == "ls-tree":
        _run_safe(run_ls_tree, argv[1:])
        return

    launcher_main()
