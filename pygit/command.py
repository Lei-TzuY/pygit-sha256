"""Top-level command router for low-level plumbing commands."""

from __future__ import annotations

import argparse
import sys
from typing import Optional, Sequence

from .cli import main as legacy_main
from .commit_plumbing import commit_tree, read_message_file, write_tree
from .entrypoint import _find_repo, dispatch as extended_dispatch
from .ref_transaction import (
    RefUpdate,
    delete_symbolic_ref,
    parse_update_records,
    set_symbolic_ref,
    symbolic_target,
    update_ref,
    update_refs,
)

_COMMANDS = {"write-tree", "commit-tree", "update-ref", "symbolic-ref"}


def _stdin_records() -> list[str]:
    return sys.stdin.read().splitlines()


def _run_write_tree(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="pygit write-tree", description="Create tree objects from the current index.")
    parser.add_argument("--missing-ok", action="store_true", help="permit intentionally absent index objects")
    parser.add_argument("--prefix", metavar="PREFIX", help="write only the subtree beneath PREFIX")
    args = parser.parse_args(list(argv))
    repo = _find_repo()
    print(write_tree(repo, missing_ok=args.missing_ok, prefix=args.prefix))
    return 0


def _run_commit_tree(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="pygit commit-tree", description="Create a commit object without updating any ref.")
    parser.add_argument("tree", metavar="TREE")
    parser.add_argument("-p", "--parent", action="append", default=[], metavar="COMMIT")
    parser.add_argument("-m", action="append", default=[], dest="messages", metavar="MESSAGE")
    parser.add_argument("-F", action="append", default=[], dest="message_files", metavar="FILE")
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
    oid = commit_tree(repo, args.tree, parents=args.parent, message="\n\n".join(parts))
    print(oid)
    return 0


def _run_update_ref(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="pygit update-ref", description="Safely update refs with compare-and-swap semantics.")
    parser.add_argument("-d", "--delete", action="store_true", help="delete REF")
    parser.add_argument("-m", metavar="REASON", default="update-ref", help="reflog message")
    parser.add_argument("--no-deref", action="store_true", help="update a symbolic ref itself instead of its target")
    parser.add_argument("--stdin", action="store_true", help="read a batch transaction from stdin")
    parser.add_argument("args", nargs="*", metavar="ARG")
    args = parser.parse_args(list(argv))
    repo = _find_repo()

    if args.stdin:
        if args.args or args.delete:
            parser.error("--stdin cannot be combined with positional refs or --delete")
        updates = parse_update_records(_stdin_records())
        update_refs(repo, updates, message=args.m, deref=not args.no_deref)
        return 0

    if args.delete:
        if len(args.args) not in {1, 2}:
            parser.error("-d requires REF [OLD]")
        update_ref(repo, args.args[0], None, old_oid=args.args[1] if len(args.args) == 2 else None, delete=True, message=args.m, deref=not args.no_deref)
        return 0

    if len(args.args) not in {2, 3}:
        parser.error("update-ref requires REF NEW [OLD]")
    update_ref(repo, args.args[0], args.args[1], old_oid=args.args[2] if len(args.args) == 3 else None, message=args.m, deref=not args.no_deref)
    return 0


def _run_symbolic_ref(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="pygit symbolic-ref", description="Read, set, or delete symbolic refs.")
    parser.add_argument("-q", "--quiet", action="store_true", help="suppress error when NAME is not symbolic")
    parser.add_argument("-d", "--delete", action="store_true", help="delete symbolic NAME")
    parser.add_argument("-m", metavar="REASON", default="symbolic-ref", help="reflog message")
    parser.add_argument("name", metavar="NAME")
    parser.add_argument("target", nargs="?", metavar="REF")
    args = parser.parse_args(list(argv))
    repo = _find_repo()

    if args.delete:
        if args.target is not None:
            parser.error("-d does not accept a target")
        try:
            delete_symbolic_ref(repo, args.name, message=args.m)
        except RuntimeError:
            if args.quiet:
                return 1
            raise
        return 0

    if args.target is not None:
        set_symbolic_ref(repo, args.name, args.target, message=args.m)
        return 0

    target = symbolic_target(repo, args.name)
    if target is None:
        if args.quiet:
            return 1
        raise RuntimeError(f"ref {args.name!r} is not a symbolic ref")
    print(target)
    return 0


def dispatch(argv: Sequence[str]) -> Optional[int]:
    if argv and argv[0] in _COMMANDS:
        try:
            if argv[0] == "write-tree":
                return _run_write_tree(argv[1:])
            if argv[0] == "commit-tree":
                return _run_commit_tree(argv[1:])
            if argv[0] == "update-ref":
                return _run_update_ref(argv[1:])
            return _run_symbolic_ref(argv[1:])
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
