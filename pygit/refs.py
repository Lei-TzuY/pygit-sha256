"""
pygit/refs.py
=============
**References** — symbolic names for commit SHAs.

In Git, branches and tags are simply text files that contain a SHA.
The current branch is pointed to by ``HEAD``, which is either:

  - a *symbolic ref*: ``ref: refs/heads/main``  (normal case)
  - a *detached HEAD*: a raw SHA                (after checkout of a commit)

Our ref storage mirrors Git exactly:

    .pygit/
      HEAD
      refs/
        heads/
          main          <- contains a 64-hex SHA
          feature-x     <- etc.
        tags/
          v1.0          <- etc.
"""

from __future__ import annotations
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List


ZERO_SHA = "0" * 64


@dataclass(frozen=True)
class ReflogEntry:
    """One recorded ref movement, newest entries are read first."""

    old_sha: str
    new_sha: str
    timestamp: int
    message: str


class RefStore:
    """
    Manages all references under ``.pygit/``.

    Parameters
    ----------
    pygit_dir : Path
        Path to the ``.pygit`` directory.
    """

    def __init__(self, pygit_dir: Path) -> None:
        self._root    = pygit_dir
        self._heads   = pygit_dir / "refs" / "heads"
        self._tags    = pygit_dir / "refs" / "tags"
        self._remotes = pygit_dir / "refs" / "remotes"
        self._stash   = pygit_dir / "refs" / "stash"
        self._head    = pygit_dir / "HEAD"
        self._logs    = pygit_dir / "logs"
        self._heads.mkdir(parents=True, exist_ok=True)
        self._tags.mkdir(parents=True, exist_ok=True)
        self._remotes.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # HEAD
    # ------------------------------------------------------------------

    def get_head(self) -> str:
        """Return HEAD content (symbolic or raw SHA)."""
        return self._head.read_text(encoding="utf-8").strip()

    def set_head_symbolic(self, branch: str, message: str = "checkout") -> None:
        """Point HEAD at a branch (e.g. 'main')."""
        old_head = self.get_head()
        old_sha = self.resolve_head()
        self._head.write_text(f"ref: refs/heads/{branch}", encoding="utf-8")
        new_sha = self.resolve_head()
        if old_head != self.get_head() or old_sha != new_sha:
            self._append_reflog(
                "HEAD",
                old_sha,
                new_sha,
                message,
                force=old_head != self.get_head(),
            )

    def set_head_detached(self, sha: str, message: str = "checkout") -> None:
        """Detach HEAD to a raw commit SHA."""
        old_head = self.get_head()
        old_sha = self.resolve_head()
        self._head.write_text(sha, encoding="utf-8")
        if old_head != sha or old_sha != sha:
            self._append_reflog("HEAD", old_sha, sha, message, force=old_head != sha)

    def is_detached(self) -> bool:
        head = self.get_head()
        return not head.startswith("ref:")

    def current_branch(self) -> Optional[str]:
        """Return current branch name, or None if HEAD is detached."""
        head = self.get_head()
        if head.startswith("ref: refs/heads/"):
            return head[len("ref: refs/heads/"):]
        return None

    def resolve_head(self) -> Optional[str]:
        """Return the commit SHA that HEAD points to, or None."""
        head = self.get_head()
        if head.startswith("ref:"):
            ref_path = self._root / head[5:].strip()
            if ref_path.exists():
                return ref_path.read_text(encoding="utf-8").strip()
            return None  # branch exists but has no commits yet
        return head  # detached HEAD — already a SHA

    # ------------------------------------------------------------------
    # Branches
    # ------------------------------------------------------------------

    def get_branch(self, name: str) -> Optional[str]:
        """Return the SHA a branch points to, or None."""
        p = self._path_under(self._heads, name)
        return p.read_text(encoding="utf-8").strip() if p.exists() else None

    def set_branch(self, name: str, sha: str, message: str = "update") -> None:
        """Update (or create) a branch to point at *sha*."""
        p = self._path_under(self._heads, name)
        old_sha = self.get_branch(name)
        old_head = self.resolve_head() if self.current_branch() == name else None
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(sha, encoding="utf-8")
        self._append_reflog(f"refs/heads/{name}", old_sha, sha, message)
        if old_head is not None or self.current_branch() == name:
            self._append_reflog("HEAD", old_head, sha, message)

    def delete_branch(self, name: str) -> None:
        p = self._path_under(self._heads, name)
        if p.exists():
            p.unlink()

    def list_branches(self) -> List[str]:
        return self._list_under(self._heads)

    # ------------------------------------------------------------------
    # Tags
    # ------------------------------------------------------------------

    def get_tag(self, name: str) -> Optional[str]:
        p = self._path_under(self._tags, name)
        return p.read_text(encoding="utf-8").strip() if p.exists() else None

    def set_tag(self, name: str, sha: str) -> None:
        p = self._path_under(self._tags, name)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(sha, encoding="utf-8")

    def list_tags(self) -> List[str]:
        return self._list_under(self._tags)

    # ------------------------------------------------------------------
    # Remote-tracking refs
    # ------------------------------------------------------------------

    def get_remote(self, remote: str, branch: str) -> Optional[str]:
        p = self._path_under(self._remotes, f"{remote}/{branch}")
        return p.read_text(encoding="utf-8").strip() if p.exists() else None

    def set_remote(self, remote: str, branch: str, sha: str) -> None:
        p = self._path_under(self._remotes, f"{remote}/{branch}")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(sha, encoding="utf-8")

    def delete_remote(self, remote: str, branch: Optional[str] = None) -> None:
        """Delete a remote-tracking branch or an entire remote namespace."""
        p = self._path_under(
            self._remotes,
            remote if branch is None else f"{remote}/{branch}",
        )
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
            self._prune_empty_parents(p.parent, self._remotes)

    def rename_remote(self, old: str, new: str) -> None:
        """Rename a remote-tracking namespace."""
        old_path = self._path_under(self._remotes, old)
        new_path = self._path_under(self._remotes, new)
        if not old_path.exists():
            return
        if new_path.exists():
            raise RuntimeError(f"Remote-tracking namespace already exists: {new}")
        new_path.parent.mkdir(parents=True, exist_ok=True)
        old_path.rename(new_path)

    def list_remotes(self, remote: Optional[str] = None) -> List[str]:
        root = self._remotes if remote is None else self._path_under(self._remotes, remote)
        if not root.exists():
            return []
        return self._list_under(root)

    # ------------------------------------------------------------------
    # Stash ref
    # ------------------------------------------------------------------

    def get_stash(self) -> Optional[str]:
        return self._stash.read_text(encoding="utf-8").strip() if self._stash.exists() else None

    def set_stash(self, sha: str, message: str = "stash") -> None:
        old_sha = self.get_stash()
        self._stash.write_text(sha, encoding="utf-8")
        self._append_reflog("refs/stash", old_sha, sha, message)

    def delete_stash(self, message: str = "stash pop") -> None:
        if self._stash.exists():
            old_sha = self.get_stash()
            self._stash.unlink()
            self._append_reflog("refs/stash", old_sha, None, message)

    # ------------------------------------------------------------------
    # Reflogs
    # ------------------------------------------------------------------

    def read_reflog(self, ref: str = "HEAD") -> List[ReflogEntry]:
        """Return ref movements newest first."""
        path = self._log_path(ref)
        if not path.exists():
            return []
        entries: List[ReflogEntry] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            metadata, _, message = line.partition("\t")
            parts = metadata.split()
            if len(parts) < 2:
                continue
            timestamp = int(parts[-2]) if len(parts) >= 4 else 0
            entries.append(ReflogEntry(parts[0], parts[1], timestamp, message))
        return list(reversed(entries))

    # ------------------------------------------------------------------
    # Generic ref resolution
    # ------------------------------------------------------------------

    def resolve(self, name: str) -> Optional[str]:
        """
        Resolve a name to a SHA.  Tries in order:

        1. Raw 64-char SHA
        2. Branch name
        3. Tag name
        4. Remote tracking branch
        5. ``HEAD``
        """
        if name == "HEAD":
            return self.resolve_head()
        if len(name) == 64 and all(c in "0123456789abcdef" for c in name.lower()):
            return name
        branch = self.get_branch(name)
        if branch:
            return branch
        tag = self.get_tag(name)
        if tag:
            return tag
        if "/" in name:
            remote, branch = name.split("/", 1)
            remote_sha = self.get_remote(remote, branch)
            if remote_sha:
                return remote_sha
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _path_under(root: Path, name: str) -> Path:
        if not name or name.startswith(("/", "\\")):
            raise ValueError("Reference name must be relative and non-empty")
        path = (root / name).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError as exc:
            raise ValueError(f"Invalid reference name: {name!r}") from exc
        return path

    @staticmethod
    def _list_under(root: Path) -> List[str]:
        if not root.exists():
            return []
        return sorted(
            p.relative_to(root).as_posix()
            for p in root.rglob("*")
            if p.is_file()
        )

    def _append_reflog(
        self,
        ref: str,
        old_sha: Optional[str],
        new_sha: Optional[str],
        message: str,
        force: bool = False,
    ) -> None:
        old = old_sha or ZERO_SHA
        new = new_sha or ZERO_SHA
        if old == new and not force:
            return
        path = self._log_path(ref)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = (
            f"{old} {new} Unknown <unknown@example.com> "
            f"{int(time.time())} +0000\t{message}\n"
        )
        with path.open("a", encoding="utf-8") as log:
            log.write(line)

    def _log_path(self, ref: str) -> Path:
        if ref == "HEAD":
            return self._logs / "HEAD"
        return self._path_under(self._logs, ref)

    @staticmethod
    def _prune_empty_parents(start: Path, stop: Path) -> None:
        current = start
        stop = stop.resolve()
        while current.resolve() != stop and current.exists():
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent
