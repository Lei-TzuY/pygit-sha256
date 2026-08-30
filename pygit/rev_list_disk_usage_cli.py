"""On-disk storage accounting for ``rev-list --disk-usage``.

The core rev-list implementation already owns revision selection while
``cat_file.object_disk_size`` owns the repository's validated loose/pack and
alternate-aware storage accounting.  This adapter deliberately composes those
two mature layers instead of duplicating either traversal or pack parsing.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from typing import Callable, Sequence

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
from .rev_list_in_commit_order_blob_limit_omitted_cli import (
    try_run_rev_list_in_commit_order_blob_limit_omitted,
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


_ROUTED_HANDLERS: tuple[Callable[[Sequence[str]], int | None], ...] = (
    try_run_rev_list_in_commit_order_blob_limit_omitted,
    try_run_rev_list_in_commit_order_blob_limit,
    try_run_rev_list_in_commit_order_object_type,
    try_run_rev_list_in_commit_order_filter_print_omitted,
    try_run_rev_list_in_commit_order,
    try_run_rev_list_blob_limit,
    try_run_rev_list_filter_print_omitted,
    try_run_rev_list_filter,
    try_run_rev_list_nul,
    try_run_rev_list_missing_print,
    try_run_rev_list_allow_promisor,
)


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


def _run_routed(argv: Sequence[str]) -> int:
    """Run the current rev-list stack without re-entering disk accounting."""

    for handler in _ROUTED_HANDLERS:
        code = handler(argv)
        if code is not None:
            return code
    return run_rev_list_header(argv)


def _run_captured(argv: Sequence[str]) -> tuple[int, str]:
    capture = io.StringIO()
    try:
        with redirect_stdout(capture):
            code = _run_routed(argv)
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

    Commit-only ``--in-commit-order`` is also a presentation ordering hint. The
    current ordered object adapter intentionally requires ``--objects`` or
    ``--objects-edge``; for commit-only disk accounting we can drop only that
    ordering hint and route through the mature commit walker because membership
    (and therefore the aggregate) is unchanged.
    """

    had_count = "--count" in argv
    object_edge = "--objects-edge" in argv
    result = [
        arg
        for arg in argv
        if arg not in _PRESENTATION_ONLY and arg != "--count"
    ]
    if (
        "--in-commit-order" in result
        and "--objects" not in result
        and "--objects-edge" not in result
    ):
        result = [arg for arg in result if arg != "--in-commit-order"]
    return result, had_count, object_edge


def _selected_oids(
    output: str, *, object_edge: bool
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Extract selected OIDs and preserve non-selection side-channel records.

    ``--objects-edge`` advertises leading excluded commits with ``-<oid>``;
    those records remain visible under ``--disk-usage`` but do not contribute
    to the byte total.  Omitted/missing records similarly retain their own
    presentation channel while never becoming local objects to size.
    """

    selected: list[str] = []
    side_records: list[str] = []
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
                side_records.append(raw_line)
                continue
        in_leading_edges = False

        if token.startswith(("~", "?")):
            side_records.append(raw_line)
            continue

        if token[:1] in {"<", ">", "-"}:
            token = token[1:]
        oid = token.lower()
        if len(oid) != 64 or any(ch not in "0123456789abcdef" for ch in oid):
            continue
        if oid in seen:
            continue
        seen.add(oid)
        selected.append(oid)

    return tuple(selected), tuple(side_records)


def run_rev_list_disk_usage(argv: Sequence[str]) -> int:
    """Run rev-list with Git-style ``--disk-usage[=human]`` accounting."""

    # Detect disk accounting before any current-stack presentation adapter.
    # Otherwise an ordered/filter handler sees the unknown --disk-usage token
    # and claims the invocation before this aggregate adapter can compose it.
    # The selection itself is then delegated back through the same mature
    # handlers with only the disk-usage token removed.
    enabled, human, cleaned = _parse_mode(argv)

    if "--help" in cleaned or "-h" in cleaned:
        code, output = _run_captured(cleaned)
        print(_decorate_help(output), end="")
        return code

    if not enabled:
        return _run_routed(cleaned)

    # Git 2.55 rejects -z with --disk-usage before traversal.  Earlier Git
    # versions accepted the pair and still printed a newline aggregate, so do
    # not infer compatibility from older runners: the current target protocol
    # is the explicit 2.55 rejection.
    if "-z" in cleaned:
        raise ValueError("-z option used with unsupported option")

    selection_argv, had_count, object_edge = _selection_argv(cleaned)
    code, output = _run_captured(selection_argv)
    if code:
        return code

    selected, side_records = _selected_oids(output, object_edge=object_edge)
    repo = _find_repo()
    total = sum(object_disk_size(repo, oid) for oid in selected)

    for record in side_records:
        print(record)

    # Native rev-list emits a zero traversal count before disk usage when
    # --count is combined with --disk-usage.  Disk accounting is independent
    # of that presentation count.
    if had_count:
        print("0")
    print(_human_size(total) if human else total)
    return 0
