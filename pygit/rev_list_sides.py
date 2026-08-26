"""Side-aware selection helpers for symmetric ``rev-list`` ranges."""

from __future__ import annotations

from typing import Sequence, Tuple

from .repo import Repository
from .rev_list import RevListEntry, rev_list


def rev_list_sides(
    repo: Repository,
    revisions: Sequence[str],
    *,
    all_refs: bool = False,
    first_parent: bool = False,
    topo_order: bool = False,
    reverse: bool = False,
    skip: int = 0,
    max_count: int = 0,
    left_only: bool = False,
    right_only: bool = False,
) -> Tuple[RevListEntry, ...]:
    """Return one side-aware ``A...B`` selection with Git-style limit ordering.

    The underlying graph engine first computes the complete symmetric
    difference with ``<`` / ``>`` markers. Side filtering is then applied
    before ``--skip`` and ``--max-count``; ``--reverse`` remains the final
    presentation transform. This ordering matches native ``git rev-list``.
    """
    if left_only and right_only:
        raise ValueError("--left-only and --right-only cannot be used together")
    if skip < 0:
        raise ValueError("--skip must be non-negative")
    if max_count < 0:
        raise ValueError("--max-count must be non-negative")

    entries = list(
        rev_list(
            repo,
            revisions,
            all_refs=all_refs,
            first_parent=first_parent,
            topo_order=topo_order,
            reverse=False,
            skip=0,
            max_count=0,
            left_right=True,
        )
    )

    if left_only:
        entries = [entry for entry in entries if entry.side == "<"]
    elif right_only:
        entries = [entry for entry in entries if entry.side == ">"]

    if skip:
        entries = entries[skip:]
    if max_count:
        entries = entries[:max_count]
    if reverse:
        entries.reverse()
    return tuple(entries)


def count_sides(entries: Sequence[RevListEntry]) -> Tuple[int, int]:
    """Return ``(left, right)`` counts for already-selected marked entries."""
    left = sum(entry.side == "<" for entry in entries)
    right = sum(entry.side == ">" for entry in entries)
    return left, right
