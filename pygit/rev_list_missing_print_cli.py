"""Plain ``rev-list --missing=print`` presentation for promisor inventories.

The richer ``print-info`` path already owns metadata-only revision/object
selection, boundary framing, counting, and SHA-domain separation.  Plain
``print`` intentionally reuses that implementation and removes only the
containing-object metadata from ``?`` records.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from typing import Optional, Sequence

from .rev_list_promisor_cli import try_run_rev_list_allow_promisor


def _is_plain_print(argv: Sequence[str]) -> bool:
    return any(arg == "--missing=print" for arg in argv)


def _to_print_info(argv: Sequence[str]) -> list[str]:
    converted: list[str] = []
    seen = 0
    for arg in argv:
        if arg == "--missing=print":
            converted.append("--missing=print-info")
            seen += 1
        else:
            converted.append(arg)
    if seen != 1:
        raise ValueError("rev-list accepts exactly one --missing action")
    return converted


def _strip_print_info(line: str) -> str:
    """Collapse ``?<oid> token=value...`` to Git's plain ``?<oid>`` form."""

    if not line.startswith("?"):
        return line
    return line.split(None, 1)[0]


def try_run_rev_list_missing_print(argv: Sequence[str]) -> Optional[int]:
    """Handle ``--missing=print`` without materializing promised objects.

    Unprefixed present-object records remain repository-visible SHA-256 ids.
    Missing records use the explicit ``?`` channel and retain the native
    transport identity already selected by ``print-info``.  No surrogate
    SHA-256 object name is invented.

    ``--objects-edge`` remains deliberately deferred because the richer
    ``print-info`` traversal does not yet model that combination either.
    """

    if not _is_plain_print(argv):
        return None

    if "--objects-edge" in argv:
        raise ValueError("--objects-edge is not yet supported with --missing=print")

    capture = io.StringIO()
    with redirect_stdout(capture):
        code = try_run_rev_list_allow_promisor(_to_print_info(argv))

    if code is None:
        raise RuntimeError("print-info promisor adapter declined --missing=print translation")

    for line in capture.getvalue().splitlines():
        print(_strip_print_info(line))
    return code
