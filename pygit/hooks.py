"""
pygit/hooks.py
==============
Git Hooks Framework
===================

Discovers and executes hook scripts under ``.pygit/hooks/``.

Supported hooks:
  - ``pre-commit``       : run before commit creation
  - ``commit-msg``       : run to validate or format commit message
  - ``pre-push``         : run before remote push
  - ``post-checkout``   : run after branch checkout
"""

from __future__ import annotations

import os
import sys
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple


class HookRunner:
    """Discovers and runs scripts under .pygit/hooks/."""

    def __init__(self, pygit_dir: Path) -> None:
        self.hooks_dir = pygit_dir / "hooks"
        self.hooks_dir.mkdir(parents=True, exist_ok=True)

    def find_hook(self, hook_name: str) -> Optional[Path]:
        """Find an executable or python hook script for *hook_name*."""
        candidates = [
            self.hooks_dir / hook_name,
            self.hooks_dir / f"{hook_name}.py",
            self.hooks_dir / f"{hook_name}.ps1",
            self.hooks_dir / f"{hook_name}.bat",
        ]
        for c in candidates:
            if c.is_file():
                return c
        return None

    def run_hook(self, hook_name: str, args: Optional[List[str]] = None) -> Tuple[int, str, str]:
        """
        Run *hook_name* if present.

        Returns ``(return_code, stdout, stderr)``.
        If hook is absent, returns ``(0, "", "")``.
        """
        hook_path = self.find_hook(hook_name)
        if not hook_path:
            return 0, "", ""

        cmd: List[str] = []
        if hook_path.suffix == ".py":
            cmd = [sys.executable, str(hook_path)]
        elif hook_path.suffix == ".ps1":
            cmd = ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(hook_path)]
        else:
            cmd = [str(hook_path)]

        if args:
            cmd.extend(args)

        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return proc.returncode, proc.stdout, proc.stderr
