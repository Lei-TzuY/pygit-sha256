"""Top-level command router.

The historical extended dispatcher remains in :mod:`pygit.entrypoint`; this
module layers object-construction plumbing on top and is the console-script
entry point.  Keeping the router small avoids coupling low-level commands to
the large porcelain CLI parser.
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional, Sequence

from .cli import main as legacy_main
from .commit_plumbing import commit_tree, read_message_file, write_tree
from .entrypoint import _find_repo, dispatch as extended_dispatch


_OBJECT_COMMANDS = {"write-tree", "commit-tree"}


def _run_write_tree(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit write-tree",
        description="Create tree objects from the current index.",
    )
    parser.add_argument(
        "--missing-ok",
        action="store_true",
        help="permit index entries whose objects are intentionally absent",
    )
    parser.add_argument(
        "--prefix",
        metavar="PREFIX",
        help="write only the subtree beneath PREFIX, stripping that prefix",
    )
    args = parser.parse_args(list(argv))

    repo = _find_repo()
    print(write_tree(repo, missing_ok=args.missing_ok, prefix=args.prefix))
    return 0


def _run_commit_tree(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit commit-tree",
        description="Create a commit object without updating any ref.",
    )
    parser.add_argument("tree", metavar="TREE")
    parser.add_argument(
        "-p",
        "--parent",
        action="append",
        default=[],
        metavar="COMMIT",
        help="add a parent commit; may be supplied multiple times",
    )
    parser.add_argument(
        "-m",
        action="append",
        default=[],
        dest="messages",
        metavar="MESSAGE",
        help="add a commit-message paragraph; may be supplied multiple times",
    )
    parser.add_argument(
        "-F",
        action="append",
        default=[],
        dest="message_files",
        metavar="FILE",
        help="read a commit-message paragraph from FILE; '-' means stdin",
    )
    args = parser.parse_args(list(argv))

    parts = [message.rstrip("\n") for message in args.messages]
    stdin_used = False
    for path in args.message_files:
        if path == "-":
            if stdin_used:
                parser.error("stdin may only be used once as a message source")
            parts.append(sys.stdin.read().rstrip("\n"))
            stdin_used = True
        else:
            parts.append(read_message_file(path).rstrip("\n"))
    if not parts:
        parts.append(sys.stdin.read().rstrip("\n"))

    repo = _find_repo()
    oid = commit_tree(
        repo,
        args.tree,
        parents=args.parent,
        message="\n\n".join(parts),
    )
    print(oid)
    return 0


def dispatch(argv: Sequence[str]) -> Optional[int]:
    """Dispatch Phase 50 object plumbing, then the existing command router."""
    if argv and argv[0] in _OBJECT_COMMANDS:
        try:
            if argv[0] == "write-tree":
                return _run_write_tree(argv[1:])
            return _run_commit_tree(argv[1:])
        except (RuntimeError, ValueError, KeyError, FileNotFoundError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    return extended_dispatch(argv)


def main() -> None:
    code = dispatch(sys.argv[1:])
    if code is None:
        legacy_main()
        return
    if code:
        raise SystemExit(code)
