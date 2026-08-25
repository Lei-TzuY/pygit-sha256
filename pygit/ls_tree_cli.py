"""CLI adapter for the modern revision-aware ``pygit ls-tree`` plumbing."""

from __future__ import annotations

import argparse
import sys
from typing import Sequence, Tuple

from .entrypoint import _find_repo
from .ls_tree import format_ls_tree, ls_tree


def _split_pathspec(argv: Sequence[str]) -> Tuple[Sequence[str], Sequence[str]]:
    values = list(argv)
    if "--" not in values:
        return values, ()
    index = values.index("--")
    return values[:index], values[index + 1 :]


def run_ls_tree(argv: Sequence[str]) -> int:
    command_argv, explicit_patterns = _split_pathspec(argv)
    parser = argparse.ArgumentParser(
        prog="pygit ls-tree",
        description="List a SHA-256 tree using the shared revision resolver.",
    )
    parser.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="recurse into subtrees",
    )
    parser.add_argument(
        "-d",
        "--directory",
        action="store_true",
        dest="directories_only",
        help="show tree entries only",
    )
    parser.add_argument(
        "-t",
        "--show-trees",
        action="store_true",
        help="include tree entries while recursing",
    )
    output = parser.add_mutually_exclusive_group()
    output.add_argument(
        "--name-only",
        action="store_true",
        help="show only path names",
    )
    output.add_argument(
        "--object-only",
        action="store_true",
        help="show only object IDs",
    )
    output.add_argument(
        "--format",
        dest="format_string",
        metavar="FORMAT",
        help="format records with %%(objectmode), %%(objecttype), %%(objectname), and %%(path)",
    )
    parser.add_argument(
        "--abbrev",
        nargs="?",
        const=12,
        type=int,
        metavar="N",
        help="abbreviate object IDs to a unique prefix (default minimum: 12)",
    )
    parser.add_argument("-z", action="store_true", help="terminate records with NUL")
    parser.add_argument("treeish", nargs="?", default="HEAD", metavar="TREE-ISH")
    parser.add_argument("pathspec", nargs="*", metavar="PATHSPEC")
    args = parser.parse_args(list(command_argv))

    patterns = tuple(args.pathspec) + tuple(explicit_patterns)
    repo = _find_repo()
    entries = ls_tree(
        repo,
        args.treeish,
        recursive=args.recursive,
        directories_only=args.directories_only,
        show_trees=args.show_trees,
        patterns=patterns,
    )
    data = format_ls_tree(
        repo,
        entries,
        name_only=args.name_only,
        object_only=args.object_only,
        format_string=args.format_string,
        abbrev=args.abbrev,
        nul_terminated=args.z,
    )
    output_stream = getattr(sys.stdout, "buffer", None)
    if output_stream is not None:
        output_stream.write(data)
    else:
        sys.stdout.write(data.decode("utf-8"))
    return 0
