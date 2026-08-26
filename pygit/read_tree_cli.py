"""Modern CLI adapter for ``pygit read-tree``."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from .read_tree_merge import read_tree_three_way
from .repo import Repository
from .tree_plumbing import read_tree


def _find_repo() -> Repository:
    for candidate in [Path.cwd(), *Path.cwd().parents]:
        if (candidate / ".pygit").is_dir():
            return Repository(str(candidate))
    raise RuntimeError(
        "fatal: not a pygit repository "
        "(or any of the parent directories): .pygit"
    )


def run_read_tree(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit read-tree",
        description="Read tree information into the index.",
    )
    parser.add_argument(
        "-m",
        "--merge",
        action="store_true",
        help="perform a three-tree trivial merge into the index",
    )
    parser.add_argument(
        "--aggressive",
        action="store_true",
        help="resolve additional deletion cases during a three-tree merge",
    )
    parser.add_argument(
        "--empty",
        action="store_true",
        help="clear the index instead of reading a tree",
    )
    parser.add_argument(
        "--prefix",
        metavar="PREFIX",
        help="add one tree beneath PREFIX without replacing existing entries",
    )
    parser.add_argument(
        "-u",
        "--update",
        action="store_true",
        help="also update the worktree for non-merge reads",
    )
    parser.add_argument("treeish", nargs="*", metavar="TREE-ISH")
    args = parser.parse_args(list(argv))

    repo = _find_repo()

    if args.merge:
        if args.empty:
            parser.error("-m cannot be combined with --empty")
        if args.prefix is not None:
            parser.error("-m cannot be combined with --prefix")
        if args.update:
            parser.error("read-tree -m -u is not supported")
        if len(args.treeish) != 3:
            parser.error("-m requires exactly three tree-ish arguments: BASE OURS THEIRS")
        read_tree_three_way(
            repo,
            args.treeish[0],
            args.treeish[1],
            args.treeish[2],
            aggressive=args.aggressive,
        )
        return 0

    if args.aggressive:
        parser.error("--aggressive requires -m")
    if args.empty:
        if args.treeish:
            parser.error("--empty cannot be combined with a tree-ish")
        treeish = None
    else:
        if len(args.treeish) != 1:
            parser.error("read-tree requires exactly one tree-ish or --empty")
        treeish = args.treeish[0]

    # Persistent conflict stages are part of the index being replaced. A normal
    # read-tree or --empty must discard them together with the old stage-0 view.
    saved_unmerged = dict(repo.index.unmerged)
    replacing = args.prefix is None
    if replacing:
        repo.index.unmerged = {}
    try:
        read_tree(
            repo,
            treeish,
            empty=args.empty,
            prefix=args.prefix,
            update_worktree=args.update,
        )
    except Exception:
        if replacing:
            repo.index.unmerged = saved_unmerged
        raise
    return 0
