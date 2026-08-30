"""Disk accounting for annotated-tag-aware object:type rev-list filters.

Phase274/277 own annotated-tag membership and presentation.  This adapter keeps
that selection authoritative, captures it in the existing line protocol, then
sums only genuine local SHA-256 objects through ``object_disk_size()``.  It does
not add another walker or object database reader.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from typing import Optional, Sequence

from . import rev_list_filter_cli as _filter
from . import rev_list_filter_tag_cli as _tag
from .cat_file import object_disk_size
from .count_objects_cli import _human_size


_IN_COMMIT_ORDER = "--in-commit-order"
_FILTER_PROVIDED = "--filter-provided-objects"


def _parse_disk_usage(argv: Sequence[str]) -> tuple[bool, bool, list[str]]:
    enabled = False
    human = False
    cleaned: list[str] = []
    for arg in argv:
        if arg == "--disk-usage":
            enabled = True
            continue
        if arg.startswith("--disk-usage="):
            value = arg.split("=", 1)[1]
            if value != "human":
                raise ValueError("--disk-usage only accepts the optional value 'human'")
            enabled = True
            human = True
            continue
        cleaned.append(arg)
    return enabled, human, cleaned


def _is_local_oid(value: str) -> bool:
    return len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)


def _selection_lines(
    repo,
    argv: Sequence[str],
    *,
    requested: str,
    filter_provided: bool,
) -> tuple[int, tuple[str, ...], frozenset[str], bool]:
    """Return exact annotated-tag-aware line selection plus object edges.

    Disk accounting needs present objects even when the user also asks for
    ``--count``.  The count flag is therefore removed from the hidden selection
    pass.  If the user did not choose a missing-object policy, ``print-info`` is
    used internally only so we can detect a missing object before emitting any
    disk-usage bytes and raise the ordinary-mode error.
    """

    had_count = "--count" in argv
    projected_user = [arg for arg in argv if arg != "--count"]
    explicit_missing = [arg for arg in projected_user if arg.startswith("--missing=")]
    if not explicit_missing:
        projected_user.append("--missing=print-info")

    projected = _tag._project_line(projected_user, requested=requested)
    parsed, _provided, edges = _filter._object_type_context(
        repo,
        projected,
        filter_provided_objects=filter_provided,
    )

    capture = io.StringIO()
    with redirect_stdout(capture):
        code = _tag.try_run_rev_list_object_type_tag(projected_user)
    if code is None:
        raise RuntimeError("annotated-tag filter adapter declined disk-usage selection")

    lines = tuple(capture.getvalue().splitlines())
    if not explicit_missing:
        for line in lines:
            if line.startswith("?"):
                native = line.split(None, 1)[0][1:] or "unknown"
                raise RuntimeError(
                    f"missing object {native}; use --missing=allow-promisor, print, or print-info"
                )

    return code, lines, edges, had_count


def try_run_rev_list_object_type_tag_disk_usage(
    argv: Sequence[str],
) -> Optional[int]:
    """Handle non-ordered annotated-tag ``object:type`` disk accounting."""

    enabled, human, cleaned = _parse_disk_usage(argv)
    if not enabled:
        return None

    requested = _tag._requested_object_type(cleaned)
    if requested is None:
        return None

    # Ordered annotated-tag work is owned by the independent Phase273 line.
    if _IN_COMMIT_ORDER in cleaned:
        return None

    # Current Git 2.55 rejects this output-mode combination.
    if "-z" in cleaned:
        raise ValueError("rev-list -z with --disk-usage is not supported by Git 2.55")

    repo = _filter._promisor._find_repo()
    discovery = _tag._parse_projection(cleaned)
    discovered_tags = _tag._annotated_tag_entries(repo, discovery)
    filter_provided = _FILTER_PROVIDED in cleaned
    emitted_tags = discovered_tags if requested == "tag" or not filter_provided else ()

    # If commit/tree/blob filtering has no annotated tag to add, leave the
    # invocation to the mature generic disk-usage path.
    if requested != "tag" and not emitted_tags:
        return None

    code, lines, edges, had_count = _selection_lines(
        repo,
        cleaned,
        requested=requested,
        filter_provided=filter_provided,
    )

    selected: list[str] = []
    selected_seen: set[str] = set()
    edge_lines: list[str] = []
    diagnostic_lines: list[str] = []

    for line in lines:
        if not line:
            continue
        prefix, oid = _filter._line_oid(line)
        if prefix == "?" or line.startswith("~"):
            diagnostic_lines.append(line)
            continue
        if prefix == "-" and oid in edges:
            edge_lines.append(line)
            continue
        if not _is_local_oid(oid):
            continue
        if oid in selected_seen:
            continue
        selected_seen.add(oid)
        selected.append(oid)

    total = sum(object_disk_size(repo, oid) for oid in selected)

    # ``--objects-edge`` remains a presentation channel, not part of the byte
    # sum.  Missing diagnostics are likewise preserved but never sized.
    for line in edge_lines:
        print(line)
    for line in diagnostic_lines:
        print(line)

    # Native rev-list prints a zero traversal count before disk usage.
    if had_count:
        print("0")
    print(_human_size(total) if human else total)
    return code
