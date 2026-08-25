"""Top-level command router for low-level plumbing commands."""

from __future__ import annotations

import argparse
import sys
from typing import Optional, Sequence

from .cli import main as legacy_main
from .commit_plumbing import commit_tree, read_message_file, write_tree
from .entrypoint import _find_repo, dispatch as extended_dispatch
from .graph_query import independent_commits, merge_bases_many, octopus_merge_bases
from .name_rev import abbreviated_oid, name_all, name_revisions
from .plumbing import is_ancestor
from .ref_transaction import (
    RefUpdate,
    delete_symbolic_ref,
    parse_update_records,
    set_symbolic_ref,
    symbolic_target,
    update_ref,
    update_refs,
)

_COMMANDS = {
    "write-tree",
    "commit-tree",
    "update-ref",
    "symbolic-ref",
    "merge-base",
    "name-rev",
}


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


def _run_merge_base(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit merge-base",
        description="Find best common ancestors across commit graphs.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--is-ancestor",
        action="store_true",
        help="test whether the first commit is an ancestor of the second",
    )
    mode.add_argument(
        "--octopus",
        action="store_true",
        help="find common ancestors shared by every supplied commit",
    )
    mode.add_argument(
        "--independent",
        action="store_true",
        help="print commits not reachable from any other supplied commit",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="print all best merge bases instead of only the first",
    )
    parser.add_argument("commit", nargs="+", metavar="COMMIT")
    args = parser.parse_args(list(argv))

    repo = _find_repo()
    if args.is_ancestor:
        if args.all or len(args.commit) != 2:
            parser.error("--is-ancestor requires exactly two commits and cannot use --all")
        return 0 if is_ancestor(repo, args.commit[0], args.commit[1]) else 1

    if args.independent:
        if args.all:
            parser.error("--independent cannot be combined with --all")
        for oid in independent_commits(repo, args.commit):
            print(oid)
        return 0

    if len(args.commit) < 2:
        parser.error("merge-base requires at least two commits")

    bases = (
        octopus_merge_bases(repo, args.commit)
        if args.octopus
        else merge_bases_many(repo, args.commit)
    )
    if not bases:
        return 1
    for oid in bases if args.all else bases[:1]:
        print(oid)
    return 0


def _run_name_rev(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit name-rev",
        description="Find symbolic names for commits from refs and ancestry paths.",
    )
    parser.add_argument("--all", action="store_true", help="name every commit reachable from selected refs")
    parser.add_argument("--tags", action="store_true", help="use only refs/tags/* as naming anchors")
    parser.add_argument(
        "--refs",
        action="append",
        default=[],
        metavar="PATTERN",
        help="limit naming anchors by glob pattern; may be supplied repeatedly",
    )
    parser.add_argument("--name-only", action="store_true", help="print only the symbolic name")
    parser.add_argument("--no-undefined", action="store_true", help="fail if any requested commit cannot be named")
    parser.add_argument("--always", action="store_true", help="fall back to a 12-hex object abbreviation")
    parser.add_argument("commit", nargs="*", metavar="COMMIT")
    args = parser.parse_args(list(argv))

    if args.all and args.commit:
        parser.error("--all cannot be combined with explicit commits")
    if not args.all and not args.commit:
        parser.error("name-rev requires at least one commit or --all")
    if args.no_undefined and args.always:
        parser.error("--no-undefined and --always are mutually exclusive")

    repo = _find_repo()
    records = (
        name_all(repo, tags_only=args.tags, ref_patterns=args.refs)
        if args.all
        else name_revisions(repo, args.commit, tags_only=args.tags, ref_patterns=args.refs)
    )

    unnamed = [record for record in records if record.name is None]
    if unnamed and args.no_undefined:
        raise RuntimeError(f"cannot describe commit {unnamed[0].oid}")

    for record in records:
        rendered = record.name
        if rendered is None:
            rendered = abbreviated_oid(record.oid) if args.always else "undefined"
        if args.name_only:
            print(rendered)
        elif args.all:
            print(f"{record.oid} {rendered}")
        else:
            print(f"{record.revision} {rendered}")
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
            if argv[0] == "symbolic-ref":
                return _run_symbolic_ref(argv[1:])
            if argv[0] == "merge-base":
                return _run_merge_base(argv[1:])
            return _run_name_rev(argv[1:])
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
