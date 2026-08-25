"""Stable installed command launcher.

Commands with binary-stdin or custom parsing needs are intercepted here before
the older argparse stack. Everything else delegates to :mod:`pygit.runtime`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from .entrypoint import _find_repo
from .fsck import fsck
from .hash_object import hash_object_data, write_object_data
from .merge_file import merge_file
from .merge_tree import merge_tree
from .mktag import make_tag
from .pack_cli import run_index_pack, run_unpack_objects
from .pack_objects_cli import run_pack_objects
from .remote_query import ls_remote, resolve_remote_url
from .runtime import main as runtime_main


def _stdin_bytes() -> bytes:
    binary = getattr(sys.stdin, "buffer", None)
    if binary is not None:
        return binary.read()
    data = sys.stdin.read()
    return data if isinstance(data, bytes) else data.encode("utf-8")


def _run_hash_object(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit hash-object",
        description="Compute SHA-256 object IDs from files or stdin.",
    )
    parser.add_argument(
        "-t",
        "--type",
        default="blob",
        choices=("blob", "tree", "commit", "tag"),
        dest="object_type",
        help="object type to hash (default: blob)",
    )
    parser.add_argument("-w", "--write", action="store_true", help="write objects to the repository")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--stdin", action="store_true", help="read one object payload from standard input")
    source.add_argument("--stdin-paths", action="store_true", help="read newline-delimited file paths from standard input")
    parser.add_argument("file", nargs="*", metavar="FILE")
    args = parser.parse_args(list(argv))

    if args.stdin and args.file:
        parser.error("--stdin cannot be combined with file arguments")
    if args.stdin_paths and args.file:
        parser.error("--stdin-paths cannot be combined with file arguments")
    if not args.stdin and not args.stdin_paths and not args.file:
        parser.error("hash-object requires FILE, --stdin, or --stdin-paths")

    repo = _find_repo() if args.write else None

    def process(data: bytes) -> None:
        oid = (
            write_object_data(repo, data, args.object_type)
            if repo is not None
            else hash_object_data(data, args.object_type)
        )
        print(oid)

    if args.stdin:
        process(_stdin_bytes())
        return 0

    if args.stdin_paths:
        for raw in sys.stdin:
            path = raw.rstrip("\r\n")
            if not path:
                continue
            process(Path(path).read_bytes())
        return 0

    for path in args.file:
        process(Path(path).read_bytes())
    return 0


def _run_fsck(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit fsck",
        description="Verify SHA-256 object storage and repository connectivity.",
    )
    scan = parser.add_mutually_exclusive_group()
    scan.add_argument("--full", action="store_true", help="check all loose and packed objects (the default)")
    scan.add_argument("--connectivity-only", action="store_true", help="walk only objects reachable from refs, index, and shallow roots")
    parser.add_argument("--unreachable", action="store_true", help="print every unreachable object instead of only dangling roots")
    parser.add_argument("--no-dangling", action="store_true", help="suppress dangling-object output")
    parser.add_argument("--strict", action="store_true", help="treat fsck warnings as a failing result")
    args = parser.parse_args(list(argv))

    repo = _find_repo()
    report = fsck(repo, connectivity_only=args.connectivity_only)

    for issue in sorted(
        report.issues,
        key=lambda item: (item.severity != "error", item.code, item.oid or "", item.source or ""),
    ):
        print(issue.render(), file=sys.stderr)

    if args.unreachable:
        selected = report.unreachable
        label = "unreachable"
    elif args.no_dangling:
        selected = set()
        label = "dangling"
    else:
        selected = report.dangling
        label = "dangling"

    for oid in sorted(selected):
        try:
            kind = repo.store.read(oid).type_name.decode("ascii", "replace")
        except Exception:
            kind = "object"
        print(f"{label} {kind} {oid}")

    failed = bool(report.errors) or (args.strict and bool(report.warnings))
    return 1 if failed else 0


def _run_merge_tree(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit merge-tree",
        description="Compute a three-way merge without changing HEAD, index, or worktree.",
    )
    parser.add_argument(
        "--write-tree",
        action="store_true",
        help="accepted for modern Git compatibility; clean merges always write the result tree",
    )
    parser.add_argument(
        "--merge-base",
        metavar="BASE",
        help="use an explicit commit-ish merge base instead of auto-detection",
    )
    parser.add_argument(
        "--allow-unrelated-histories",
        action="store_true",
        help="allow histories with no common ancestor",
    )
    parser.add_argument(
        "--messages",
        action="store_true",
        help="print merge-base and conflict diagnostics in addition to the result",
    )
    parser.add_argument(
        "--name-only",
        action="store_true",
        help="print only conflicted path names on an unclean merge",
    )
    parser.add_argument("ours", metavar="OURS")
    parser.add_argument("theirs", metavar="THEIRS")
    args = parser.parse_args(list(argv))

    result = merge_tree(
        _find_repo(),
        args.ours,
        args.theirs,
        base=args.merge_base,
        allow_unrelated_histories=args.allow_unrelated_histories,
    )
    if result.clean:
        assert result.tree_oid is not None
        print(result.tree_oid)
        if args.messages:
            print(f"base {result.base_oid or '(none)'}")
            print("clean")
        return 0

    if args.messages:
        print(f"base {result.base_oid or '(none)'}")
    for conflict in result.conflicts:
        if args.name_only:
            print(conflict.path)
        else:
            print(f"CONFLICT ({conflict.reason})\t{conflict.path}")
    return 1


def _run_merge_file(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit merge-file",
        description="Merge two file versions relative to a common base.",
    )
    parser.add_argument(
        "-p",
        "--stdout",
        action="store_true",
        help="write the merged result to stdout instead of replacing CURRENT",
    )
    parser.add_argument(
        "--diff3",
        action="store_true",
        help="include the base version in conflict regions",
    )
    parser.add_argument(
        "--marker-size",
        type=int,
        default=7,
        metavar="N",
        help="use N-character conflict markers (default: 7)",
    )
    parser.add_argument(
        "-L",
        action="append",
        default=[],
        dest="labels",
        metavar="LABEL",
        help="override current/base/other conflict labels; may be supplied up to three times",
    )
    parser.add_argument("current", metavar="CURRENT")
    parser.add_argument("base", metavar="BASE")
    parser.add_argument("other", metavar="OTHER")
    args = parser.parse_args(list(argv))

    if len(args.labels) > 3:
        parser.error("-L may be supplied at most three times")
    defaults = [args.current, args.base, args.other]
    for index, label in enumerate(args.labels):
        defaults[index] = label

    result = merge_file(
        args.current,
        args.base,
        args.other,
        labels=(defaults[0], defaults[1], defaults[2]),
        style="diff3" if args.diff3 else "merge",
        marker_size=args.marker_size,
        write_current=not args.stdout,
    )
    if args.stdout:
        output = getattr(sys.stdout, "buffer", None)
        if output is not None:
            output.write(result.data)
        else:
            sys.stdout.write(result.data.decode("utf-8"))
    return min(result.conflicts, 127)


def _run_mktag(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit mktag",
        description="Validate and create one annotated tag object from stdin.",
    )
    parser.parse_args(list(argv))
    print(make_tag(_find_repo(), _stdin_bytes()))
    return 0


def _run_ls_remote(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit ls-remote",
        description="List refs advertised by a smart HTTP remote without fetching objects.",
    )
    parser.add_argument("--heads", action="store_true", help="show branch refs only")
    parser.add_argument("--tags", action="store_true", help="show tag refs only")
    parser.add_argument(
        "--refs",
        action="store_true",
        dest="refs_only",
        help="omit pseudorefs such as HEAD and peeled tag helper refs",
    )
    parser.add_argument("--symref", action="store_true", help="show symbolic-ref targets such as HEAD")
    parser.add_argument(
        "--exit-code",
        action="store_true",
        help="return status 2 when no refs match the requested filters",
    )
    parser.add_argument(
        "--get-url",
        action="store_true",
        help="resolve and print the remote URL without contacting it",
    )
    parser.add_argument("repository", metavar="REPOSITORY")
    parser.add_argument("pattern", nargs="*", metavar="PATTERN")
    args = parser.parse_args(list(argv))

    try:
        url = resolve_remote_url(args.repository)
        repo = None
    except KeyError:
        repo = _find_repo()
        url = resolve_remote_url(args.repository, repo)

    if args.get_url:
        print(url)
        return 0

    result = ls_remote(
        url,
        repo=repo,
        heads=args.heads,
        tags=args.tags,
        refs_only=args.refs_only,
        patterns=args.pattern,
    )
    if args.symref:
        for name, target in result.symrefs:
            print(f"ref: {target}\t{name}")
    for ref in result.refs:
        print(f"{ref.oid}\t{ref.name}")
    if args.exit_code and not result.refs:
        return 2
    return 0


def main() -> None:
    argv = sys.argv[1:]
    commands = {
        "hash-object",
        "fsck",
        "merge-tree",
        "merge-file",
        "mktag",
        "ls-remote",
        "index-pack",
        "unpack-objects",
        "pack-objects",
    }
    if not argv or argv[0] not in commands:
        runtime_main()
        return

    try:
        if argv[0] == "hash-object":
            code = _run_hash_object(argv[1:])
        elif argv[0] == "fsck":
            code = _run_fsck(argv[1:])
        elif argv[0] == "merge-tree":
            code = _run_merge_tree(argv[1:])
        elif argv[0] == "merge-file":
            code = _run_merge_file(argv[1:])
        elif argv[0] == "mktag":
            code = _run_mktag(argv[1:])
        elif argv[0] == "ls-remote":
            code = _run_ls_remote(argv[1:])
        elif argv[0] == "index-pack":
            code = run_index_pack(argv[1:])
        elif argv[0] == "unpack-objects":
            code = run_unpack_objects(argv[1:])
        else:
            code = run_pack_objects(argv[1:])
    except (RuntimeError, ValueError, KeyError, FileNotFoundError, FileExistsError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        code = 1
    if code:
        raise SystemExit(code)
