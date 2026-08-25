"""Stable top-level application entrypoint.

Most commands continue through :mod:`pygit.launcher`.  This small front door is
reserved for nested command grammars that must be recognized before legacy
argparse dispatch; Phase 72 adds ``reflog expire`` without changing existing
``reflog [REF]`` behavior.
"""

from __future__ import annotations

import sys

from .launcher import main as launcher_main
from .reflog_expire_cli import run_reflog_expire


def main() -> None:
    argv = sys.argv[1:]
    if len(argv) >= 2 and argv[0] == "reflog" and argv[1] == "expire":
        try:
            code = run_reflog_expire(argv[2:])
        except (
            RuntimeError,
            ValueError,
            KeyError,
            FileNotFoundError,
            FileExistsError,
            IsADirectoryError,
            OSError,
        ) as exc:
            print(f"error: {exc}", file=sys.stderr)
            code = 1
        if code:
            raise SystemExit(code)
        return
    launcher_main()
