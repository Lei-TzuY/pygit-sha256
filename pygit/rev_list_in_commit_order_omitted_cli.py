"""Compose ``rev-list --in-commit-order`` with filter omission framing.

Phase264 already applies ``blob:none`` directly to the structured ordered object
inventory. Phase265 deliberately reuses that implementation for traversal and
the Phase253-257 omission helpers for the independent ``~<oid>`` channel instead
of introducing a second object walker or a second wire protocol. Phase270
extends the same composition to native ``-z + --count`` framing.
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from typing import Optional, Sequence

from . import rev_list_filter_omitted_cli as _omitted
from . import rev_list_in_commit_order_cli as _ordered
from . import rev_list_promisor_cli as _promisor


_IN_COMMIT_ORDER = "--in-commit-order"
_FILTER_PRINT_OMITTED = "--filter-print-omitted"


def _filter_spec(argv: Sequence[str]) -> str:
    filters = [arg for arg in argv if arg.startswith("--filter=")]
    if not filters:
        raise ValueError("--filter-print-omitted requires --filter")
    if len(filters) != 1:
        raise ValueError(
            "rev-list --in-commit-order accepts exactly one --filter action in this phase"
        )
    spec = filters[0].split("=", 1)[1]
    if spec != "blob:none":
        raise ValueError(
            "rev-list --in-commit-order with --filter-print-omitted currently supports only --filter=blob:none"
        )
    return spec


def _omission_projection(argv: Sequence[str]) -> list[str]:
    """Project ordered traversal back onto the mature omission inventory parser."""

    projected = [
        arg
        for arg in argv
        if arg not in {_IN_COMMIT_ORDER, _FILTER_PRINT_OMITTED}
    ]
    if not any(arg.startswith("--missing=") for arg in projected):
        projected.append("--missing=allow-promisor")
    return projected


def try_run_rev_list_in_commit_order_filter_print_omitted(
    argv: Sequence[str],
) -> Optional[int]:
    """Render ordered traversal, omissions, missing diagnostics, then count."""

    if _IN_COMMIT_ORDER not in argv or _FILTER_PRINT_OMITTED not in argv:
        return None
    if argv.count(_FILTER_PRINT_OMITTED) != 1:
        raise ValueError("rev-list accepts --filter-print-omitted at most once")

    spec = _filter_spec(argv)
    cleaned = [arg for arg in argv if arg != _FILTER_PRINT_OMITTED]

    capture = io.StringIO()
    with redirect_stdout(capture):
        code = _ordered.try_run_rev_list_in_commit_order(cleaned)
    if code is None:
        raise RuntimeError(
            "in-commit-order rev-list adapter declined omitted-object projection"
        )
    if code:
        sys.stdout.write(capture.getvalue())
        return code

    repo = _promisor._find_repo()
    omitted = _omitted._omitted_local_oids(
        repo,
        _omission_projection(argv),
        spec=spec,
    )

    projected_output = capture.getvalue()
    if "-z" in cleaned:
        count_line = None
        if "--count" in cleaned:
            traversal, missing, count_line = _omitted._partition_projected_nul_count(
                projected_output
            )
        else:
            traversal, missing = _omitted._partition_projected_nul(projected_output)
        for record in traversal:
            sys.stdout.write(record)
        for oid in omitted:
            sys.stdout.write(f"~{oid}\n")
        for record in missing:
            sys.stdout.write(record)
        if count_line is not None:
            sys.stdout.write(f"{count_line}\n")
        return code

    traversal, missing, count_line = _omitted._partition_projected_lines(
        projected_output.splitlines(),
        count_mode="--count" in cleaned,
    )
    for line in traversal:
        print(line)
    for oid in omitted:
        print(f"~{oid}")
    for line in missing:
        print(line)
    if count_line is not None:
        print(count_line)
    return code
