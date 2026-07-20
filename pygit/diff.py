"""
pygit/diff.py
=============
Unified-diff utilities.

Used by ``Repository.diff()`` to produce human-readable change output
in the same format as ``git diff``.
"""

from __future__ import annotations
import difflib


def unified_diff(
    a_bytes: bytes,
    b_bytes: bytes,
    from_label: str = "a",
    to_label: str = "b",
    context: int = 3,
) -> str:
    """
    Produce a unified-diff string between two byte payloads.

    Binary files fall back to a one-line "Binary files … differ" notice.

    Parameters
    ----------
    a_bytes     : "before" content
    b_bytes     : "after" content
    from_label  : label shown on the ``---`` line
    to_label    : label shown on the ``+++`` line
    context     : unchanged lines to show around each hunk

    Returns
    -------
    str  — empty string when the two inputs are identical.
    """
    if a_bytes == b_bytes:
        return ""

    try:
        a_lines = a_bytes.decode().splitlines(keepends=True)
        b_lines = b_bytes.decode().splitlines(keepends=True)
    except UnicodeDecodeError:
        return f"Binary files {from_label} and {to_label} differ\n"

    return "".join(difflib.unified_diff(
        a_lines, b_lines,
        fromfile=from_label,
        tofile=to_label,
        n=context,
    ))
