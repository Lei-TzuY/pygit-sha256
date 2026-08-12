"""
pygit/submodule.py
==================
Git Submodules Engine
=====================

Parses and manages submodules configured in ``.pygitmodules``.

Format of ``.pygitmodules``:

    [submodule "libs/foo"]
        path = libs/foo
        url = https://github.com/example/foo.git
"""

from __future__ import annotations

import configparser
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class SubmoduleSpec:
    def __init__(self, name: str, path: str, url: str) -> None:
        self.name = name
        self.path = path
        self.url = url


class SubmoduleManager:
    """Manages submodules for a Repository."""

    def __init__(self, worktree: Path) -> None:
        self.worktree = worktree
        self.config_path = worktree / ".pygitmodules"

    def list_submodules(self) -> List[SubmoduleSpec]:
        if not self.config_path.exists():
            return []

        parser = configparser.ConfigParser()
        parser.read(self.config_path, encoding="utf-8")

        specs: List[SubmoduleSpec] = []
        for sec in parser.sections():
            if sec.startswith('submodule "') and sec.endswith('"'):
                name = sec[11:-1]
                path = parser.get(sec, "path", fallback="")
                url = parser.get(sec, "url", fallback="")
                if path and url:
                    specs.append(SubmoduleSpec(name, path, url))
        return specs

    def add_submodule(self, url: str, path: Optional[str] = None) -> SubmoduleSpec:
        if not path:
            path = Path(url).stem

        name = path
        parser = configparser.ConfigParser()
        if self.config_path.exists():
            parser.read(self.config_path, encoding="utf-8")

        sec_name = f'submodule "{name}"'
        if not parser.has_section(sec_name):
            parser.add_section(sec_name)
        parser.set(sec_name, "path", path)
        parser.set(sec_name, "url", url)

        with open(self.config_path, "w", encoding="utf-8") as f:
            parser.write(f)

        # Create submodule directory
        sub_dir = self.worktree / path
        sub_dir.mkdir(parents=True, exist_ok=True)
        return SubmoduleSpec(name, path, url)
