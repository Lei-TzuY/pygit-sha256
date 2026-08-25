"""Extended command dispatcher for plumbing commands kept outside cli.py."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

from .cli import main as legacy_main
from .plumbing import is_ancestor, list_refs, merge_bases, peel_oid, verify_ref
from .ref_query import check_ref_format, format_ref, query_refs
from .repo import Repository
from .tree_plumbing import make_tree, read_tree


_EXTRA_COMMANDS = {
    "merge-base",
    "show-ref",
    "for-each-ref",
    "check-ref-format",
    "mktree",
    "read-tree",
}


def _find_repo() -> Repository:
    for candidate in [Path.cwd(), *Path.cwd().parents]:
        if (candidate / ".pygit").is_dir():
            return Repository(str(candidate))
    raise RuntimeError(
        "fatal: not a pygit repository "
        "(or any of the parent directories): .pygit"
    )


def _run_merge_base(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit merge-base",
        description="Find best common ancestors between two commits.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--all",
        action="store_true",
        help="output all merge bases instead of one",
    )
    mode.add_argument(
        "--is-ancestor",
        action="store_true",
        help="test whether the first commit is an ancestor of the second",
    )
    parser.add_argument("commit", nargs=2, metavar="COMMIT")
    args = parser.parse_args(list(argv))

    repo = _find_repo()
    left, right = args.commit
    if args.is_ancestor:
        return 0 if is_ancestor(repo, left, right) else 1

    bases = merge_bases(repo, left, right)
    if not bases:
        return 1
    for sha in bases if args.all else bases[:1]:
        print(sha)
    return 0


def _run_show_ref(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit show-ref",
        description="List references in the local repository.",
    )
    parser.add_argument("--head", action="store_true", help="include HEAD")
    parser.add_argument("--heads", action="store_true", help="show local branches only")
    parser.add_argument("--tags", action="store_true", help="show tags only")
    parser.add_argument(
        "-d",
        "--dereference",
        action="store_true",
        help="dereference annotated tags",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="require exact, fully-qualified ref names",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="suppress normal output; useful with --verify",
    )
    parser.add_argument("pattern", nargs="*", metavar="PATTERN")
    args = parser.parse_args(list(argv))

    repo = _find_repo()
    missing = False

    if args.verify:
        if not args.pattern:
            parser.error("--verify requires at least one exact ref name")
        records = []
        for refname in args.pattern:
            try:
                record = verify_ref(repo, refname)
            except KeyError:
                missing = True
                continue
            records.append(record)
    else:
        records = list_refs(
            repo,
            include_head=args.head,
            heads=args.heads,
            tags=args.tags,
            patterns=args.pattern,
        )

    if not args.quiet:
        for oid, refname in records:
            print(f"{oid} {refname}")
            if args.dereference:
                peeled = peel_oid(repo, oid)
                if peeled != oid:
                    print(f"{peeled} {refname}^{{}}")

    if missing or not records:
        return 1
    return 0


def _run_for_each_ref(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit for-each-ref",
        description="Filter, sort, and format references.",
    )
    parser.add_argument(
        "--count",
        type=int,
        metavar="N",
        help="show at most N refs after sorting",
    )
    parser.add_argument(
        "--sort",
        action="append",
        default=[],
        metavar="KEY",
        help="sort by refname/objectname/objecttype/date field; prefix '-' for descending",
    )
    parser.add_argument(
        "--format",
        default="%(objectname) %(refname)",
        metavar="FORMAT",
        help="format output using %(...)-style ref atoms",
    )
    parser.add_argument(
        "--contains",
        nargs="?",
        const="HEAD",
        metavar="COMMIT",
        help="only refs whose tip contains COMMIT (default HEAD)",
    )
    parser.add_argument(
        "--no-contains",
        nargs="?",
        const="HEAD",
        metavar="COMMIT",
        help="only refs whose tip does not contain COMMIT",
    )
    parser.add_argument(
        "--merged",
        nargs="?",
        const="HEAD",
        metavar="COMMIT",
        help="only refs whose tip is reachable from COMMIT",
    )
    parser.add_argument(
        "--no-merged",
        nargs="?",
        const="HEAD",
        metavar="COMMIT",
        help="only refs whose tip is not reachable from COMMIT",
    )
    parser.add_argument("pattern", nargs="*", metavar="PATTERN")
    args = parser.parse_args(list(argv))

    repo = _find_repo()
    records = query_refs(
        repo,
        patterns=args.pattern,
        sort_keys=args.sort,
        count=args.count,
        contains=args.contains,
        no_contains=args.no_contains,
        merged=args.merged,
        no_merged=args.no_merged,
    )
    for record in records:
        print(format_ref(record, args.format))
    return 0


def _run_check_ref_format(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit check-ref-format",
        description="Validate a reference name.",
    )
    parser.add_argument(
        "--allow-onelevel",
        action="store_true",
        help="permit a refname with no slash",
    )
    parser.add_argument(
        "--branch",
        action="store_true",
        help="validate a branch name (one-level names allowed; leading '-' rejected)",
    )
    parser.add_argument(
        "--normalize",
        action="store_true",
        help="remove leading/repeated slashes before validation and print the result",
    )
    parser.add_argument("refname", metavar="REFNAME")
    args = parser.parse_args(list(argv))

    checked = check_ref_format(
        args.refname,
        allow_onelevel=args.allow_onelevel,
        branch=args.branch,
        normalize=args.normalize,
    )
    if args.normalize:
        print(checked)
    return 0


def _run_mktree(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit mktree",
        description="Build a tree object from ls-tree style input on stdin.",
    )
    parser.add_argument(
        "--missing",
        action="store_true",
        help="allow entries that reference objects not present locally",
    )
    parser.add_argument(
        "-z",
        action="store_true",
        help="read NUL-terminated records instead of newline-terminated records",
    )
    args = parser.parse_args(list(argv))

    raw = sys.stdin.read()
    records = raw.split("\x00") if args.z else raw.splitlines()
    if args.z and records and records[-1] == "":
        records.pop()
    repo = _find_repo()
    print(make_tree(repo, records, missing=args.missing))
    return 0


def _run_read_tree(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit read-tree",
        description="Read tree information into the index.",
    )
    parser.add_argument(
        "--empty",
        action="store_true",
        help="clear the index instead of reading a tree",
    )
    parser.add_argument(
        "--prefix",
        metavar="PREFIX",
        help="add the tree under PREFIX without replacing existing index entries",
    )
    parser.add_argument(
        "-u",
        "--update",
        action="store_true",
        help="also update the working tree; requires a clean repository",
    )
    parser.add_argument("treeish", nargs="?", metavar="TREE-ISH")
    args = parser.parse_args(list(argv))

    repo = _find_repo()
    read_tree(
        repo,
        args.treeish,
        empty=args.empty,
        prefix=args.prefix,
        update_worktree=args.update,
    )
    return 0


def dispatch(argv: Sequence[str]) -> Optional[int]:
    """Run an extended command, or return ``None`` for the legacy CLI."""
    if not argv or argv[0] not in _EXTRA_COMMANDS:
        return None

    try:
        if argv[0] == "merge-base":
            return _run_merge_base(argv[1:])
        if argv[0] == "show-ref":
            return _run_show_ref(argv[1:])
        if argv[0] == "for-each-ref":
            return _run_for_each_ref(argv[1:])
        if argv[0] == "check-ref-format":
            return _run_check_ref_format(argv[1:])
        if argv[0] == "mktree":
            return _run_mktree(argv[1:])
        return _run_read_tree(argv[1:])
    except (RuntimeError, ValueError, KeyError, FileNotFoundError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def main() -> None:
    code = dispatch(sys.argv[1:])
    if code is None:
        legacy_main()
        return
    if code:
        raise SystemExit(code)
