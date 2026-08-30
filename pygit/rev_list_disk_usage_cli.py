"""On-disk storage accounting for ``rev-list --disk-usage``.

The core rev-list implementation already owns revision selection while
``cat_file.object_disk_size`` owns the repository's validated loose/pack and
alternate-aware storage accounting.  This adapter deliberately composes those
two mature layers instead of duplicating either traversal or pack parsing.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from typing import Sequence

from .cat_file import object_disk_size
from .count_objects_cli import _human_size
from .entrypoint import _find_repo
from .rev_list_filter_blob_limit_cli import try_run_rev_list_blob_limit
from .rev_list_filter_omitted_cli import try_run_rev_list_filter_print_omitted
from .rev_list_filter_cli import try_run_rev_list_filter
from .rev_list_header_cli import run_rev_list_header
from .rev_list_in_commit_order_blob_limit_cli import (
    try_run_rev_list_in_commit_order_blob_limit,
)
from .rev_list_in_commit_order_cli import try_run_rev_list_in_commit_order
from .rev_list_in_commit_order_object_type_cli import (
    try_run_rev_list_in_commit_order_object_type,
)
from .rev_list_in_commit_order_omitted_cli import (
    try_run_rev_list_in_commit_order_filter_print_omitted,
)
from .rev_list_missing_print_cli import try_run_rev_list_missing_print
from .rev_list_nul_cli import try_run_rev_list_nul
from .rev_list_promisor_cli import try_run_rev_list_allow_promisor


_PRESENTATION_ONLY = {
    "--header",
    "--timestamp",
    "--parents",
    "--children",
    "--no-object-names",
}


def _decorate_help(text: str) -> str:
    if "--disk-usage" in text:
        return text
    needle = "  --header"
    index = text.find(needle)
    if index < 0:
        return text + (
            "\n  --disk-usage[=human]"
            "  print selected objects' on-disk storage instead of normal output\n"
        )
    line_end = text.find("\n", index)
    if line_end < 0:
        line_end = len(text)
    insertion = (
        "\n  --disk-usage[=human]"
        "  print selected objects' on-disk storage instead of normal output"
    )
    return text[:line_end] + insertion + text[line_end:]


def _run_captured(argv: Sequence[str]) -> tuple[int, str]:
    capture = io.StringIO()
    try:
        with redirect_stdout(capture):
            code = run_rev_list_header(argv)
    except SystemExit as exc:
        raw_code = exc.code
        code = raw_code if isinstance(raw_code, int) else 1
    return code, capture.getvalue()


def _parse_mode(argv: Sequence[str]) -> tuple[bool, bool, list[str]]:
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


def _selection_argv(argv: Sequence[str]) -> tuple[list[str], bool, bool]:
    """Return arguments that expose the exact objects whose size is counted.

    Formatting flags do not affect Git's disk accounting and are stripped so
    the captured stream remains one object per line. ``--count`` is special:
    native Git prints a zero count before the disk-usage result, but selection
    itself still has to be materialized for accounting.
    """

    had_count = "--count" in argv
    object_edge = "--objects-edge" in argv
    result = [
        arg
        for arg in argv
        if arg not in _PRESENTATION_ONLY and arg != "--count"
    ]
    return result, had_count, object_edge


def _selected_oids(output: str, *, object_edge: bool) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Extract selected OIDs while separating leading ``--objects-edge`` records."""

    selected: list[str] = []
    edges: list[str] = []
    seen: set[str] = set()
    in_leading_edges = object_edge

    for raw_line in output.splitlines():
        if not raw_line:
            continue
        token = raw_line.split(None, 1)[0]
        marked = token.startswith("-")
        if in_leading_edges and marked:
            candidate = token[1:].lower()
            if len(candidate) == 64 and all(ch in "0123456789abcdef" for ch in candidate):
                edges.append(candidate)
                continue
        in_leading_edges = False

        if token[:1] in {"<", ">", "-"}:
            token = token[1:]
        oid = token.lower()
        if len(oid) != 64 or any(ch not in "0123456789abcdef" for ch in oid):
            continue
        if oid in seen:
            continue
        seen.add(oid)
        selected.append(oid)

    return tuple(selected), tuple(edges)


def run_rev_list_disk_usage(argv: Sequence[str]) -> int:
    """Run rev-list with Git-style ``--disk-usage[=human]`` accounting."""

    ordered_blob_limit_code = try_run_rev_list_in_commit_order_blob_limit(argv)
    if ordered_blob_limit_code is not None:
        return ordered_blob_limit_code

    ordered_object_type_code = try_run_rev_list_in_commit_order_object_type(argv)
    if ordered_object_type_code is not None:
        return ordered_object_type_code

    ordered_omitted_code = try_run_rev_list_in_commit_order_filter_print_omitted(argv)
    if ordered_omitted_code is not None:
        return ordered_omitted_code

    in_commit_order_code = try_run_rev_list_in_commit_order(argv)
    if in_commit_order_code is not None:
        return in_commit_order_code

    blob_limit_code = try_run_rev_list_blob_limit(argv)
    if blob_limit_code is not None:
        return blob_limit_code

    omitted_code = try_run_rev_list_filter_print_omitted(argv)
    if omitted_code is not None:
        return omitted_code

    filter_code = try_run_rev_list_filter(argv)
    if filter_code is not None:
        return filter_code

    nul_code = try_run_rev_list_nul(argv)
    if nul_code is not None:
        return nul_code

    missing_print_code = try_run_rev_list_missing_print(argv)
    if missing_print_code is not None:
        return missing_print_code

    promisor_code = try_run_rev_list_allow_promisor(argv)
    if promisor_code is not None:
        return promisor_code

    enabled, human, cleaned = _parse_mode(argv)

    if "--help" in cleaned or "-h" in cleaned:
        code, output = _run_captured(cleaned)
        print(_decorate_help(output), end="")
        return code

    if not enabled:
        return run_rev_list_header(cleaned)

    selection_argv, had_count, object_edge = _selection_argv(cleaned)
    code, output = _run_captured(selection_argv)
    if code:
        return code

    selected, edges = _selected_oids(output, object_edge=object_edge)
    repo = _find_repo()
    total = sum(object_disk_size(repo, oid) for oid in selected)

    # Native rev-list keeps --objects-edge's advertised edge records even
    # though excluded edge objects do not contribute to the byte total.
    for oid in edges:
        print(f"-{oid}")

    # Git currently emits a zero count before disk usage when --count is also
    # requested. Preserve that observable plumbing protocol rather than
    # inventing a count of the captured selection.
    if had_count:
        print("0")
    print(_human_size(total) if human else total)
    return 0
