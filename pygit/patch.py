"""
pygit/patch.py
==============
Hunk Patch Parser and Application Engine
========================================

Parses unified diffs into individual hunks and applies selected hunks.
"""

from __future__ import annotations

import re
from typing import List, Tuple


class Hunk:
    """Represents a single diff hunk header and its line diffs."""

    def __init__(
        self,
        header: str,
        old_start: int,
        old_count: int,
        new_start: int,
        new_count: int,
        lines: List[str],
    ) -> None:
        self.header = header
        self.old_start = old_start
        self.old_count = old_count
        self.new_start = new_start
        self.new_count = new_count
        self.lines = lines

    def format_text(self) -> str:
        return f"{self.header}\n" + "\n".join(self.lines)


def parse_diff_hunks(diff_text: str) -> List[Tuple[str, List[Hunk]]]:
    """
    Parse unified diff text into a list of (file_path, hunks).
    """
    results: List[Tuple[str, List[Hunk]]] = []
    current_file = ""
    current_hunks: List[Hunk] = []

    lines = diff_text.splitlines()
    i = 0

    while i < len(lines):
        line = lines[i]
        if line.startswith("--- a/") or line.startswith("+++ b/"):
            if line.startswith("+++ b/"):
                current_file = line[6:].strip()
            i += 1
            continue

        if line.startswith("@@ "):
            m = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            if m:
                old_start = int(m.group(1))
                old_count = int(m.group(2)) if m.group(2) else 1
                new_start = int(m.group(3))
                new_count = int(m.group(4)) if m.group(4) else 1
                header = line

                hunk_lines = []
                i += 1
                while i < len(lines) and not lines[i].startswith("@@ ") and not lines[i].startswith("--- a/"):
                    hunk_lines.append(lines[i])
                    i += 1

                hunk = Hunk(header, old_start, old_count, new_start, new_count, hunk_lines)
                current_hunks.append(hunk)

                if i < len(lines) and lines[i].startswith("--- a/"):
                    if current_file:
                        results.append((current_file, current_hunks))
                    current_file = ""
                    current_hunks = []
                continue
        i += 1

    if current_file and current_hunks:
        results.append((current_file, current_hunks))

    return results


def apply_hunks_to_lines(original_lines: List[str], hunks: List[Hunk]) -> List[str]:
    """Apply a subset of accepted hunks to original file lines."""
    result = list(original_lines)
    offset = 0

    for hunk in hunks:
        old_idx = (hunk.old_start - 1) + offset
        
        # Build replacement block
        new_block = []
        old_len = 0
        for line in hunk.lines:
            if line.startswith(" ") or line.startswith("+"):
                new_block.append(line[1:])
            if line.startswith(" ") or line.startswith("-"):
                old_len += 1

        result[old_idx : old_idx + old_len] = new_block
        offset += len(new_block) - old_len

    return result
