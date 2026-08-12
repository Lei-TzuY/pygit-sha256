"""
pygit/worktree.py
=================
Linked Worktrees Manager
========================

Manages multiple working tree locations sharing the primary repository's object store.

Directory Layout:
-----------------
Main Repo: .pygit/worktrees/<name>/
  - HEAD
  - index
  - gitdir -> /path/to/linked/worktree
Linked Worktree: <path>/.git -> gitdir: /path/to/main/.pygit/worktrees/<name>
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import List, Optional


class WorktreeSpec:
    def __init__(self, name: str, path: Path, head_ref: str, is_main: bool = False) -> None:
        self.name = name
        self.path = path
        self.head_ref = head_ref
        self.is_main = is_main


class WorktreeManager:
    """Manages linked working trees."""

    def __init__(self, pygit_dir: Path, main_worktree: Path) -> None:
        self.pygit_dir = pygit_dir
        self.main_worktree = main_worktree
        self.worktrees_dir = pygit_dir / "worktrees"
        self.worktrees_dir.mkdir(parents=True, exist_ok=True)

    def list_worktrees(self) -> List[WorktreeSpec]:
        specs: List[WorktreeSpec] = [
            WorktreeSpec("main", self.main_worktree, "HEAD", is_main=True)
        ]
        if not self.worktrees_dir.exists():
            return specs

        for wt_dir in self.worktrees_dir.iterdir():
            if wt_dir.is_dir():
                gitdir_file = wt_dir / "gitdir"
                head_file = wt_dir / "HEAD"
                wt_path = Path(gitdir_file.read_text(encoding="utf-8").strip()) if gitdir_file.exists() else wt_dir
                head_ref = head_file.read_text(encoding="utf-8").strip() if head_file.exists() else "HEAD"
                specs.append(WorktreeSpec(wt_dir.name, wt_path, head_ref, is_main=False))
        return specs

    def add_worktree(self, path: Path, target_ref: str) -> WorktreeSpec:
        """Create a new linked working tree at *path*."""
        path = path.resolve()
        path.mkdir(parents=True, exist_ok=True)

        name = path.name
        wt_dir = self.worktrees_dir / name
        wt_dir.mkdir(parents=True, exist_ok=True)

        (wt_dir / "gitdir").write_text(str(path), encoding="utf-8")
        (wt_dir / "HEAD").write_text(f"ref: refs/heads/{target_ref}", encoding="utf-8")

        # Create pointer file in linked worktree
        pointer_file = path / ".pygit"
        pointer_file.write_text(f"gitdir: {wt_dir.resolve()}", encoding="utf-8")

        return WorktreeSpec(name, path, f"refs/heads/{target_ref}", is_main=False)

    def remove_worktree(self, path: Path) -> bool:
        """Remove a linked worktree."""
        path = path.resolve()
        name = path.name
        wt_dir = self.worktrees_dir / name

        if wt_dir.exists():
            shutil.rmtree(wt_dir, ignore_errors=True)

        pointer_file = path / ".pygit"
        if pointer_file.exists():
            pointer_file.unlink(missing_ok=True)
        return True
