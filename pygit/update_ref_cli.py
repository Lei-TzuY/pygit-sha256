"""Stable CLI adapter for transactional ``update-ref`` plumbing."""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from .entrypoint import _find_repo
from .ref_batch import update_refs_batch
from .ref_transaction import parse_update_records, update_ref, update_refs
from .update_ref_protocol import parse_update_records_z


def _stdin_records() -> list[str]:
    return sys.stdin.read().splitlines()


def run_update_ref(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit update-ref",
        description="Safely update refs with compare-and-swap semantics.",
    )
    parser.add_argument("-d", "--delete", action="store_true", help="delete REF")
    parser.add_argument("-m", metavar="REASON", default="update-ref", help="reflog message")
    parser.add_argument(
        "--no-deref",
        action="store_true",
        help="update a symbolic ref itself instead of its target",
    )
    parser.add_argument("--stdin", action="store_true", help="read a batch transaction from stdin")
    parser.add_argument("-z", action="store_true", help="use NUL-delimited --stdin command fields")
    parser.add_argument(
        "--batch-updates",
        action="store_true",
        help="allow rejectable ref-state conflicts without discarding valid updates",
    )
    parser.add_argument("args", nargs="*", metavar="ARG")
    args = parser.parse_args(list(argv))
    repo = _find_repo()

    if args.z and not args.stdin:
        parser.error("-z requires --stdin")
    if args.batch_updates and not args.stdin:
        parser.error("--batch-updates requires --stdin")

    if args.stdin:
        if args.args or args.delete:
            parser.error("--stdin cannot be combined with positional refs or --delete")
        if args.z:
            binary_input = getattr(sys.stdin, "buffer", None)
            if binary_input is None:
                raise RuntimeError("update-ref -z requires a binary stdin stream")
            updates = parse_update_records_z(binary_input.read())
        else:
            updates = parse_update_records(_stdin_records())

        if args.batch_updates:
            rejections = update_refs_batch(
                repo,
                updates,
                message=args.m,
                deref=not args.no_deref,
            )
            for rejection in rejections:
                # Native Git keeps rejection diagnostics LF-delimited even when
                # the input protocol uses -z.
                print(rejection.format())
        else:
            update_refs(repo, updates, message=args.m, deref=not args.no_deref)
        return 0

    if args.delete:
        if len(args.args) not in {1, 2}:
            parser.error("-d requires REF [OLD]")
        update_ref(
            repo,
            args.args[0],
            None,
            old_oid=args.args[1] if len(args.args) == 2 else None,
            delete=True,
            message=args.m,
            deref=not args.no_deref,
        )
        return 0

    if len(args.args) not in {2, 3}:
        parser.error("update-ref requires REF NEW [OLD]")
    update_ref(
        repo,
        args.args[0],
        args.args[1],
        old_oid=args.args[2] if len(args.args) == 3 else None,
        message=args.m,
        deref=not args.no_deref,
    )
    return 0
