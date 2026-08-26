"""CLI adapter for advanced ``rev-list`` commit/object traversal."""

from __future__ import annotations

import argparse
from typing import Sequence

from .entrypoint import _find_repo
from .rev_list import rev_list
from .rev_list_children import rev_list_children
from .rev_list_object_names import rev_list_named_objects, rev_list_object_edges
from .rev_list_parents import parent_oids
from .rev_list_sides import count_sides, rev_list_sides


def _format_commit_line(oid: str, *, marker: str = "", related=()) -> str:
    prefix = marker or ""
    suffix = "" if not related else " " + " ".join(related)
    return f"{prefix}{oid}{suffix}"


def run_rev_list(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit rev-list",
        description="List commits or their object closure without touching repository state.",
    )
    parser.add_argument("--all", action="store_true", help="start from every commit-ish ref")
    parser.add_argument("--count", action="store_true", help="print only the number of selected commits or objects")
    parser.add_argument(
        "--objects",
        action="store_true",
        help="include selected commits' required tree/blob closure with pathname annotations",
    )
    parser.add_argument(
        "--objects-edge",
        action="store_true",
        help="like --objects, and prefix uninteresting boundary commits with '-'",
    )
    parser.add_argument(
        "--no-object-names",
        action="store_true",
        help="suppress pathname annotations in --objects/--objects-edge output",
    )
    relation_group = parser.add_mutually_exclusive_group()
    relation_group.add_argument(
        "--parents",
        action="store_true",
        help="print each selected commit followed by its parent object IDs",
    )
    relation_group.add_argument(
        "--children",
        action="store_true",
        help="print each selected commit followed by its child object IDs",
    )
    parser.add_argument(
        "--left-right",
        action="store_true",
        help="mark commits from the left/right side of one A...B symmetric range",
    )
    side_group = parser.add_mutually_exclusive_group()
    side_group.add_argument(
        "--left-only",
        action="store_true",
        help="emit only the left side of one A...B symmetric range",
    )
    side_group.add_argument(
        "--right-only",
        action="store_true",
        help="emit only the right side of one A...B symmetric range",
    )
    parser.add_argument("--first-parent", action="store_true", help="follow only the first parent of merge commits")
    parser.add_argument("--topo-order", action="store_true", help="never show a parent before a selected child")
    parser.add_argument("--reverse", action="store_true", help="reverse the final selected commit order")
    parser.add_argument("--skip", type=int, default=0, metavar="N", help="skip the first N selected commits")
    parser.add_argument(
        "-n",
        "--max-count",
        type=int,
        default=0,
        metavar="N",
        help="limit output to at most N selected commits",
    )
    parser.add_argument(
        "revision",
        nargs="*",
        metavar="REV",
        help="positive REV, ^REV exclusion, A..B range, or one A...B symmetric range",
    )
    args = parser.parse_args(list(argv))

    object_mode = args.objects or args.objects_edge
    side_mode = args.left_right or args.left_only or args.right_only
    repo = _find_repo()

    child_entries = None
    child_map = {}
    if args.children:
        child_entries = rev_list_children(
            repo,
            args.revision,
            all_refs=args.all,
            first_parent=args.first_parent,
            topo_order=args.topo_order,
            reverse=args.reverse,
            skip=args.skip,
            max_count=args.max_count,
            left_right=side_mode,
        )
        child_map = {entry.oid: entry.children for entry in child_entries}

    if object_mode:
        if side_mode:
            parser.error(
                "--objects/--objects-edge cannot be combined with "
                "--left-right/--left-only/--right-only"
            )

        objects = rev_list_named_objects(
            repo,
            args.revision,
            all_refs=args.all,
            first_parent=args.first_parent,
            topo_order=args.topo_order,
            reverse=args.reverse,
            skip=args.skip,
            max_count=args.max_count,
        )

        if args.objects_edge:
            for oid in rev_list_object_edges(
                repo,
                args.revision,
                all_refs=args.all,
                first_parent=args.first_parent,
                topo_order=args.topo_order,
            ):
                print(f"-{oid}")

        if args.count:
            print(len(objects))
            return 0

        for entry in objects:
            if entry.type_name == "commit" and args.parents:
                print(_format_commit_line(entry.oid, related=parent_oids(repo, entry.oid)))
            elif entry.type_name == "commit" and args.children:
                print(_format_commit_line(entry.oid, related=child_map.get(entry.oid, ())))
            elif args.no_object_names or entry.path is None:
                print(entry.oid)
            else:
                print(f"{entry.oid} {entry.path}")
        return 0

    if side_mode:
        entries = rev_list_sides(
            repo,
            args.revision,
            all_refs=args.all,
            first_parent=args.first_parent,
            topo_order=args.topo_order,
            reverse=args.reverse,
            skip=args.skip,
            max_count=args.max_count,
            left_only=args.left_only,
            right_only=args.right_only,
        )
    elif child_entries is not None:
        entries = child_entries
    else:
        entries = rev_list(
            repo,
            args.revision,
            all_refs=args.all,
            first_parent=args.first_parent,
            topo_order=args.topo_order,
            reverse=args.reverse,
            skip=args.skip,
            max_count=args.max_count,
            left_right=False,
        )

    if args.count:
        if args.left_right:
            left, right = count_sides(entries)
            print(f"{left}\t{right}")
        else:
            print(len(entries))
        return 0

    for entry in entries:
        marker = entry.side if args.left_right else ""
        if args.parents:
            related = parent_oids(repo, entry.oid)
        elif args.children:
            related = child_map.get(entry.oid, ())
        else:
            related = ()
        print(_format_commit_line(entry.oid, marker=marker or "", related=related))
    return 0
