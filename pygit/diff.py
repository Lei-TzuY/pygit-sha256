"""
pygit/diff.py
=============
Unified-diff and stat utilities.

Used by ``Repository.diff()`` and ``Repository.show()`` to produce
human-readable change output in the same format as ``git diff``.
"""

from __future__ import annotations
import difflib
from typing import List, Tuple


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _try_decode(raw: bytes) -> Tuple[bool, List[str]]:
    """
    Try to decode *raw* as UTF-8 text.

    Returns (is_text, lines).  Lines keep their line endings so that
    ``difflib.unified_diff`` can emit correct ``\\ No newline`` notices.
    """
    try:
        return True, raw.decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        return False, []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def unified_diff(
    a_bytes: bytes,
    b_bytes: bytes,
    from_label: str = "a",
    to_label: str = "b",
    context: int = 3,
) -> str:
    """
    Produce a unified-diff string between two byte payloads.

    Binary files fall back to a one-line ``"Binary files … differ"`` notice.

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

    a_is_text, a_lines = _try_decode(a_bytes)
    b_is_text, b_lines = _try_decode(b_bytes)

    if not a_is_text or not b_is_text:
        return f"Binary files {from_label} and {to_label} differ\n"

    return "".join(difflib.unified_diff(
        a_lines, b_lines,
        fromfile=from_label,
        tofile=to_label,
        n=context,
    ))


def diff_stat(
    a_bytes: bytes,
    b_bytes: bytes,
    path: str,
) -> Tuple[str, int, int]:
    """
    Compute a ``git diff --stat``-style summary for one file.

    Returns ``(bar_line, insertions, deletions)`` where *bar_line* is a
    formatted string like::

        hello.txt | 3 +++

    The caller is responsible for aligning columns across multiple files.
    """
    if a_bytes == b_bytes:
        return (f" {path} | 0", 0, 0)

    a_is_text, a_lines = _try_decode(a_bytes)
    b_is_text, b_lines = _try_decode(b_bytes)

    if not a_is_text or not b_is_text:
        return (f" {path} | Bin", 0, 0)

    insertions = 0
    deletions = 0
    for line in difflib.unified_diff(a_lines, b_lines, n=0):
        if line.startswith("+") and not line.startswith("+++"):
            insertions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1

    return (f" {path} | {insertions + deletions}", insertions, deletions)


def format_stat_block(entries: List[Tuple[str, int, int]]) -> str:
    """
    Render a list of ``(path, insertions, deletions)`` triples as a
    ``git diff --stat`` block::

         hello.txt | 3 +++
         world.txt | 1 -
         2 files changed, 3 insertions(+), 1 deletion(-)

    Parameters
    ----------
    entries : list of ``(path, insertions, deletions)`` from :func:`diff_stat`
    """
    if not entries:
        return ""

    total_ins = sum(ins for _, ins, _ in entries)
    total_del = sum(d for _, _, d in entries)
    max_path_len = max(len(p) for p, _, _ in entries)
    max_changes = max((ins + d) for _, ins, d in entries) or 1
    bar_width = min(40, max_changes)

    lines: List[str] = []
    for path, ins, dels in entries:
        total = ins + dels
        bar_size = round(total * bar_width / max_changes) if max_changes else 0
        ins_bar = "+" * min(bar_size, ins * bar_width // (total or 1) + 1)
        del_bar = "-" * max(0, bar_size - len(ins_bar))
        bar = (ins_bar + del_bar)[:bar_size]
        lines.append(f" {path:<{max_path_len}} | {total:>4} {bar}")

    n = len(entries)
    summary_parts = []
    if total_ins:
        summary_parts.append(f"{total_ins} insertion{'s' if total_ins != 1 else ''}(+)")
    if total_del:
        summary_parts.append(f"{total_del} deletion{'s' if total_del != 1 else ''}(-)")
    suffix = ", ".join(summary_parts) if summary_parts else "0 changes"
    lines.append(f" {n} file{'s' if n != 1 else ''} changed, {suffix}")

    return "\n".join(lines) + "\n"
