"""Stable top-level application entrypoint.

Most commands continue through :mod:`pygit.launcher`.  Commands that need a
modern nested/custom grammar are handled here before the legacy argparse stack.
"""

from __future__ import annotations

import sys
from typing import Sequence

from .cat_file_cli import run_cat_file
from .count_objects_cli import run_count_objects
from .for_each_ref_cli import run_for_each_ref
from .gc_cli import run_gc
from .launcher import main as launcher_main
from .ls_tree_cli import run_ls_tree
from .merge_base_cli import run_merge_base
from .reflog_expire_cli import run_reflog_expire
from .reflog_show_cli import run_reflog_show
from .show_ref_cli import run_show_ref
from .update_ref_cli import run_update_ref


_ERRORS = (
    RuntimeError,
    ValueError,
    KeyError,
    FileNotFoundError,
    FileExistsError,
    IsADirectoryError,
    OSError,
)


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
            show_argv = argv[2:] if len(argv) >= 2 and argv[1] == "show" else argv[1:]
            _run_safe(run_reflog_show, show_argv)
        return

    if argv and argv[0] == "gc":
        _run_safe(run_gc, argv[1:])
        return

    if argv and argv[0] == "ls-tree":
        _run_safe(run_ls_tree, argv[1:])
        return

    if argv and argv[0] == "show-ref":
        _run_safe(run_show_ref, argv[1:])
        return

    if argv and argv[0] == "cat-file":
        _run_safe(run_cat_file, argv[1:])
        return

    if argv and argv[0] == "count-objects":
        _run_safe(run_count_objects, argv[1:])
        return

    if argv and argv[0] == "merge-base":
        _run_safe(run_merge_base, argv[1:])
        return

    if argv and argv[0] == "for-each-ref":
        _run_safe(run_for_each_ref, argv[1:])
        return

    if argv and argv[0] == "update-ref":
        _run_safe(run_update_ref, argv[1:])
        return

    launcher_main()
