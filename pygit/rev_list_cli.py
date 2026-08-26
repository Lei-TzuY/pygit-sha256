"""CLI adapter for advanced ``rev-list`` commit/object traversal."""

from __future__ import annotations

import argparse
from typing import Sequence

from .entrypoint import _find_repo
from .rev_list import rev_list
from .rev_list_boundary import boundary_children, rev_list_boundary
from .rev_list_children import rev_list_children
from .rev_list_object_names import rev_list_named_objects, rev_list_object_edges
from .rev_list_oldest import (
    rev_list_oldest,
    rev_list_oldest_boundary,
    rev_list_oldest_children,
    rev_list_oldest_named_objects,
)
from .rev_list_parent_filter import (
    rev_list_parent_filter,
    rev_list_parent_filter_boundary,
    rev_list_parent_filter_children,
    rev_list_parent_filter_named_objects,
)
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
    parser.add_argument(
        "--boundary",
        action="store_true",
        help="include excluded commits at the boundary of the visible commit set",
    )
    parser.add_argument(
        "--min-parents",
        type=int,
        default=0,
        metavar="N",
        help="show only commits with at least N parents",
    )
    parser.add_argument(
        "--no-min-parents",
        dest="min_parents",
        action="store_const",
        const=0,
        help="reset the minimum-parent limit",
    )
    parser.add_argument(
        "--merges",
        dest="min_parents",
        action="store_const",
        const=2,
        help="show only merge commits (same as --min-parents=2)",
    )
    parser.add_argument(
        "--max-parents",
        type=int,
        default=-1,
        metavar="N",
        help="show only commits with at most N parents; negative means unlimited",
    )
    parser.add_argument(
        "--no-max-parents",
        dest="max_parents",
        action="store_const",
        const=-1,
        help="reset the maximum-parent limit",
    )
    parser.add_argument(
        "--no-merges",
        dest="max_parents",
        action="store_const",
        const=1,
        help="exclude merge commits (same as --max-parents=1)",
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
        "--max-count-oldest",
        type=int,
        default=None,
        metavar="N",
        help="limit output to the oldest N commits that would otherwise be shown",
    )
    parser.add_argument(
        "revision",
        nargs="*",
        metavar="REV",
        help="positive REV, ^REV exclusion, A..B range, or one A...B symmetric range",
    )
    args = parser.parse_args(list(argv))

    if args.max_count_oldest is not None:
        if args.max_count_oldest < 0:
            parser.error("--max-count-oldest must be non-negative")
        if args.max_count or args.skip:
            parser.error("--max-count-oldest cannot be used together with --max-count or --skip")

    object_mode = args.objects or args.objects_edge
    side_mode = args.left_right or args.left_only or args.right_only
    parent_filter_mode = args.min_parents > 0 or args.max_parents >= 0
    oldest_mode = args.max_count_oldest is not None
    repo = _find_repo()

    child_entries = None
    child_map = {}
    if args.children:
        if oldest_mode:
            child_entries = rev_list_oldest_children(
                repo,
                args.revision,
                max_count_oldest=args.max_count_oldest,
                all_refs=args.all,
                first_parent=args.first_parent,
                topo_order=args.topo_order,
                reverse=args.reverse,
                left_right=side_mode,
                left_only=args.left_only,
                right_only=args.right_only,
                min_parents=args.min_parents,
                max_parents=args.max_parents,
            )
        elif parent_filter_mode:
            child_entries = rev_list_parent_filter_children(
                repo,
                args.revision,
                all_refs=args.all,
                first_parent=args.first_parent,
                topo_order=args.topo_order,
                reverse=args.reverse,
                skip=args.skip,
                max_count=args.max_count,
                left_right=side_mode,
                left_only=args.left_only,
                right_only=args.right_only,
                min_parents=args.min_parents,
                max_parents=args.max_parents,
            )
        else:
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

    boundary_entries = None
    boundary_child_map = {}
    if args.boundary:
        if oldest_mode:
            boundary_entries = rev_list_oldest_boundary(
                repo,
                args.revision,
                max_count_oldest=args.max_count_oldest,
                all_refs=args.all,
                first_parent=args.first_parent,
                topo_order=args.topo_order,
                reverse=args.reverse,
                side_mode=side_mode,
                left_only=args.left_only,
                right_only=args.right_only,
                min_parents=args.min_parents,
                max_parents=args.max_parents,
            )
        elif parent_filter_mode:
            boundary_entries = rev_list_parent_filter_boundary(
                repo,
                args.revision,
                all_refs=args.all,
                first_parent=args.first_parent,
                topo_order=args.topo_order,
                reverse=args.reverse,
                skip=args.skip,
                max_count=args.max_count,
                side_mode=side_mode,
                left_only=args.left_only,
                right_only=args.right_only,
                min_parents=args.min_parents,
                max_parents=args.max_parents,
            )
        else:
            boundary_entries = rev_list_boundary(
                repo,
                args.revision,
                all_refs=args.all,
                first_parent=args.first_parent,
                topo_order=args.topo_order,
                reverse=args.reverse,
                skip=args.skip,
                max_count=args.max_count,
                side_mode=side_mode,
                left_only=args.left_only,
                right_only=args.right_only,
            )
        if args.children:
            boundary_child_map = boundary_children(
                repo,
                boundary_entries,
                first_parent=args.first_parent,
            )

    if object_mode:
        if side_mode:
            parser.error(
                "--objects/--objects-edge cannot be combined with "
                "--left-right/--left-only/--right-only"
            )

        if oldest_mode:
            objects = rev_list_oldest_named_objects(
                repo,
                args.revision,
                max_count_oldest=args.max_count_oldest,
                all_refs=args.all,
                first_parent=args.first_parent,
                topo_order=args.topo_order,
                reverse=args.reverse,
                min_parents=args.min_parents,
                max_parents=args.max_parents,
            )
        elif parent_filter_mode:
            objects = rev_list_parent_filter_named_objects(
                repo,
                args.revision,
                all_refs=args.all,
                first_parent=args.first_parent,
                topo_order=args.topo_order,
                reverse=args.reverse,
                skip=args.skip,
                max_count=args.max_count,
                min_parents=args.min_parents,
                max_parents=args.max_parents,
            )
        else:
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

        edge_oids = ()
        if args.objects_edge:
            edge_oids = rev_list_object_edges(
                repo,
                args.revision,
                all_refs=args.all,
                first_parent=args.first_parent,
                topo_order=args.topo_order,
            )
            for oid in edge_oids:
                print(f"-{oid}")

        boundary_oids = () if boundary_entries is None else tuple(
            entry.oid for entry in boundary_entries if entry.boundary
        )

        if args.count:
            print(len(objects) + len(boundary_oids))
            return 0

        commit_objects = {entry.oid: entry for entry in objects if entry.type_name == "commit"}
        other_objects = [entry for entry in objects if entry.type_name != "commit"]
        emitted_edges = set(edge_oids)

        if boundary_entries is not None:
            for relation_entry in boundary_entries:
                oid = relation_entry.oid
                if relation_entry.boundary:
                    if oid in emitted_edges:
                        continue
                    if args.parents:
                        related = parent_oids(repo, oid)
                    elif args.children:
                        related = boundary_child_map.get(oid, ())
                    else:
                        related = ()
                    print(_format_commit_line(oid, marker="-", related=related))
                    continue

                if oid not in commit_objects:
                    continue
                if args.parents:
                    related = parent_oids(repo, oid)
                elif args.children:
                    related = child_map.get(oid, ())
                else:
                    related = ()
                print(_format_commit_line(oid, related=related))
        else:
            for entry in commit_objects.values():
                if args.parents:
                    print(_format_commit_line(entry.oid, related=parent_oids(repo, entry.oid)))
                elif args.children:
                    print(_format_commit_line(entry.oid, related=child_map.get(entry.oid, ())))
                else:
                    print(entry.oid)

        for entry in other_objects:
            if args.no_object_names or entry.path is None:
                print(entry.oid)
            else:
                print(f"{entry.oid} {entry.path}")
        return 0

    if boundary_entries is not None:
        entries = boundary_entries
    elif child_entries is not None:
        entries = child_entries
    elif oldest_mode:
        entries = rev_list_oldest(
            repo,
            args.revision,
            max_count_oldest=args.max_count_oldest,
            all_refs=args.all,
            first_parent=args.first_parent,
            topo_order=args.topo_order,
            reverse=args.reverse,
            left_right=side_mode,
            left_only=args.left_only,
            right_only=args.right_only,
            min_parents=args.min_parents,
            max_parents=args.max_parents,
        )
    elif parent_filter_mode:
        entries = rev_list_parent_filter(
            repo,
            args.revision,
            all_refs=args.all,
            first_parent=args.first_parent,
            topo_order=args.topo_order,
            reverse=args.reverse,
            skip=args.skip,
            max_count=args.max_count,
            left_right=side_mode,
            left_only=args.left_only,
            right_only=args.right_only,
            min_parents=args.min_parents,
            max_parents=args.max_parents,
        )
    elif side_mode:
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
        is_boundary = bool(getattr(entry, "boundary", False))
        marker = "-" if is_boundary else (entry.side if args.left_right else "")
        if args.parents:
            related = parent_oids(repo, entry.oid)
        elif args.children and is_boundary:
            related = boundary_child_map.get(entry.oid, ())
        elif args.children:
            related = child_map.get(entry.oid, ())
        else:
            related = ()
        print(_format_commit_line(entry.oid, marker=marker or "", related=related))
    return 0
