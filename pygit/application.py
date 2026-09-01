"""Stable top-level application entrypoint.

Most commands continue through :mod:`pygit.launcher`.  Commands that need a
modern nested/custom grammar are handled here before the legacy argparse stack.
"""

from __future__ import annotations

import re
import sys
from typing import Sequence

from .cat_file_cli import run_cat_file
from .checkout_index_cli import run_checkout_index
from .checkout_previous_cli import run_checkout_previous
from .commit_graph_cli import run_commit_graph
from .count_objects_cli import run_count_objects
from .for_each_ref_cli import run_for_each_ref
from .fsck_cli import run_fsck
from .gc_cli import run_gc
from .launcher import main as launcher_main
from .ls_files_cli import run_ls_files
from .ls_tree_cli import run_ls_tree
from .merge_base_cli import run_merge_base
from .pack_refs_cli import run_pack_refs
from .read_tree_cli import run_read_tree
from .reflog_expire_cli import run_reflog_expire
from .reflog_show_cli import run_reflog_show
from .rev_list_disk_usage_cli import run_rev_list_disk_usage
from .show_ref_cli import run_show_ref
from .status_cli import run_status
from .update_ref_cli import run_update_ref
from .verify_pack_cli import run_verify_pack


_ERRORS = (
    RuntimeError,
    ValueError,
    KeyError,
    FileNotFoundError,
    FileExistsError,
    IsADirectoryError,
    OSError,
)

_PREVIOUS_CHECKOUT_SELECTOR = re.compile(r"^@\{-\d+\}$")


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
    if (
        len(argv) == 2
        and argv[0] == "checkout"
        and _PREVIOUS_CHECKOUT_SELECTOR.fullmatch(argv[1]) is not None
    ):
        _run_safe(run_checkout_previous, argv[1:])
        return

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

    if argv and argv[0] == "fsck":
        _run_safe(run_fsck, argv[1:])
        return

    if argv and argv[0] == "status":
        _run_safe(run_status, argv[1:])
        return

    if argv and argv[0] == "ls-tree":
        _run_safe(run_ls_tree, argv[1:])
        return

    if argv and argv[0] == "ls-files":
        _run_safe(run_ls_files, argv[1:])
        return

    if argv and argv[0] == "read-tree":
        _run_safe(run_read_tree, argv[1:])
        return

    if argv and argv[0] == "show-ref":
        _run_safe(run_show_ref, argv[1:])
        return

    if argv and argv[0] == "cat-file":
        _run_safe(run_cat_file, argv[1:])
        return

    if argv and argv[0] == "checkout-index":
        _run_safe(run_checkout_index, argv[1:])
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

    if argv and argv[0] == "verify-pack":
        _run_safe(run_verify_pack, argv[1:])
        return

    if argv and argv[0] == "commit-graph":
        _run_safe(run_commit_graph, argv[1:])
        return

    if argv and argv[0] == "pack-refs":
        _run_safe(run_pack_refs, argv[1:])
        return

    if argv and argv[0] == "rev-list":
        _run_safe(run_rev_list_disk_usage, argv[1:])
        return

    launcher_main()
