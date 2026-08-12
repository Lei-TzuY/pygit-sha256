"""
pygit/tools.py
==============
External Diff & Merge Tool Helper Framework
===========================================

Helper routines for invoking visual diff tools (difftool) and interactive conflict resolution tools (mergetool).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Optional, Tuple


class DiffMergeTool:
    """Helper framework for launching external diff and merge tools."""

    def __init__(self, worktree: Path) -> None:
        self.worktree = worktree

    def run_difftool(self, diff_text: str, tool_cmd: Optional[str] = None) -> List[str]:
        """
        Format diff output for difftool viewing.
        """
        lines = ["[pygit difftool output]"]
        for line in diff_text.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                lines.append(f"  + {line[1:]}")
            elif line.startswith("-") and not line.startswith("---"):
                lines.append(f"  - {line[1:]}")
            else:
                lines.append(f"    {line}")
        return lines

    def run_mergetool(self, conflicts: List[str]) -> List[Tuple[str, str]]:
        """
        Inspect unmerged conflict files and return resolution status.
        """
        results = []
        for path in conflicts:
            p = self.worktree / path
            if p.exists():
                text = p.read_text(encoding="utf-8")
                # Auto-resolve if markers cleared
                if "<<<<<<<" not in text and "=======" not in text and ">>>>>>>" not in text:
                    results.append((path, "resolved"))
                else:
                    results.append((path, "conflict"))
        return results
