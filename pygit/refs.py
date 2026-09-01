"""
pygit/refs.py
=============
Reference storage for loose, symbolic, and packed refs.

Loose refs live below ``.pygit/refs`` and shadow entries from
``.pygit/packed-refs``. ``HEAD`` may be symbolic or detached. The public API
keeps the original branch/tag/remote helpers while making packed storage
transparent to callers.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Set

from .packed_refs import (
    PackedRef,
    list_packed_refnames,
    packed_ref_value,
    read_packed_refs,
    remove_packed_refs,
    write_packed_refs,
)


ZERO_SHA = "0" * 64
_HEX = frozenset("0123456789abcdef")
_MAX_SYMREF_DEPTH = 32
_CHECKOUT_MOVING_TO_PREFIX = "checkout: moving to "


@dataclass(frozen=True)
class ReflogEntry:
    """One recorded ref movement, newest entries are read first."""

    old_sha: str
    new_sha: str
    timestamp: int
    message: str


class RefStore:
    """Manage references under one ``.pygit`` directory."""

    def __init__(self, pygit_dir: Path) -> None:
        self._root = pygit_dir
        self._heads = pygit_dir / "refs" / "heads"
        self._tags = pygit_dir / "refs" / "tags"
        self._remotes = pygit_dir / "refs" / "remotes"
        self._stash = pygit_dir / "refs" / "stash"
        self._head = pygit_dir / "HEAD"
        self._logs = pygit_dir / "logs"
        self._heads.mkdir(parents=True, exist_ok=True)
        self._tags.mkdir(parents=True, exist_ok=True)
        self._remotes.mkdir(parents=True, exist_ok=True)

    @property
    def refs_dir(self) -> Path:
        return self._root / "refs"

    # ------------------------------------------------------------------
    # Generic direct/symbolic resolution
    # ------------------------------------------------------------------

    def _resolve_refname(self, refname: str) -> Optional[str]:
        current = refname
        seen: Set[str] = set()
        for _ in range(_MAX_SYMREF_DEPTH):
            if current in seen:
                raise RuntimeError(f"Symbolic ref cycle while resolving {refname!r}")
            seen.add(current)

            if current == "HEAD":
                path = self._head
            else:
                if not current.startswith("refs/"):
                    raise ValueError(f"Expected fully-qualified ref name: {current!r}")
                path = self._path_under(self._root / "refs", current[len("refs/") :])

            if path.exists():
                raw = path.read_text(encoding="utf-8").strip()
                if raw.startswith("ref: "):
                    target = raw[5:].strip()
                    if not target.startswith("refs/"):
                        raise RuntimeError(f"Malformed symbolic ref {current}: {raw!r}")
                    current = target
                    continue
                if not self._is_oid(raw):
                    raise RuntimeError(f"Malformed ref {current}: expected a 64-hex object ID")
                return raw.lower()

            if current != "HEAD":
                return packed_ref_value(self._root, current)
            return None

        raise RuntimeError(f"Symbolic ref chain is too deep while resolving {refname!r}")

    @staticmethod
    def _is_oid(value: str) -> bool:
        return len(value) == 64 and all(char in _HEX for char in value.lower())

    # ------------------------------------------------------------------
    # HEAD
    # ------------------------------------------------------------------

    def get_head(self) -> str:
        """Return HEAD content (symbolic or raw SHA)."""
        return self._head.read_text(encoding="utf-8").strip()

    @staticmethod
    def _native_checkout_message(
        message: str,
        old_head: str,
        old_sha: Optional[str],
    ) -> str:
        """Upgrade historical ``moving to`` checkout messages at write time.

        Old repositories may already contain the historical pygit spelling and
        remain readable.  New HEAD checkout movements record the source before
        HEAD is changed, matching native Git's ``moving from X to Y`` shape.
        """
        if not message.startswith(_CHECKOUT_MOVING_TO_PREFIX):
            return message
        target = message[len(_CHECKOUT_MOVING_TO_PREFIX) :]
        if old_head.startswith("ref: refs/heads/"):
            source = old_head[len("ref: refs/heads/") :]
        else:
            source = old_sha or old_head
        if not source:
            return message
        return f"checkout: moving from {source} to {target}"

    def set_head_symbolic(self, branch: str, message: str = "checkout") -> None:
        """Point HEAD at a branch (e.g. ``main``)."""
        old_head = self.get_head()
        old_sha = self.resolve_head()
        message = self._native_checkout_message(message, old_head, old_sha)
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
        message = self._native_checkout_message(message, old_head, old_sha)
        self._head.write_text(sha, encoding="utf-8")
        if old_head != sha or old_sha != sha:
            self._append_reflog("HEAD", old_sha, sha, message, force=old_head != sha)

    def is_detached(self) -> bool:
        return not self.get_head().startswith("ref:")

    def current_branch(self) -> Optional[str]:
        head = self.get_head()
        if head.startswith("ref: refs/heads/"):
            return head[len("ref: refs/heads/") :]
        return None

    def resolve_head(self) -> Optional[str]:
        return self._resolve_refname("HEAD")

    # ------------------------------------------------------------------
    # Branches
    # ------------------------------------------------------------------

    def get_branch(self, name: str) -> Optional[str]:
        self._path_under(self._heads, name)  # traversal validation
        return self._resolve_refname(f"refs/heads/{name}")

    def set_branch(self, name: str, sha: str, message: str = "update") -> None:
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
            self._prune_empty_parents(p.parent, self._heads)
        remove_packed_refs(self._root, [f"refs/heads/{name}"])

    def list_branches(self) -> List[str]:
        loose = set(self._list_under(self._heads))
        packed = {
            name[len("refs/heads/") :]
            for name in list_packed_refnames(self._root, "refs/heads/")
        }
        return sorted(loose | packed)

    # ------------------------------------------------------------------
    # Tags
    # ------------------------------------------------------------------

    def get_tag(self, name: str) -> Optional[str]:
        self._path_under(self._tags, name)
        return self._resolve_refname(f"refs/tags/{name}")

    def set_tag(self, name: str, sha: str) -> None:
        p = self._path_under(self._tags, name)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(sha, encoding="utf-8")

    def delete_tag(self, name: str) -> None:
        p = self._path_under(self._tags, name)
        if p.exists():
            p.unlink()
            self._prune_empty_parents(p.parent, self._tags)
        remove_packed_refs(self._root, [f"refs/tags/{name}"])

    def list_tags(self) -> List[str]:
        loose = set(self._list_under(self._tags))
        packed = {
            name[len("refs/tags/") :]
            for name in list_packed_refnames(self._root, "refs/tags/")
        }
        return sorted(loose | packed)

    # ------------------------------------------------------------------
    # Remote-tracking refs
    # ------------------------------------------------------------------

    def get_remote(self, remote: str, branch: str) -> Optional[str]:
        self._path_under(self._remotes, f"{remote}/{branch}")
        return self._resolve_refname(f"refs/remotes/{remote}/{branch}")

    def set_remote(self, remote: str, branch: str, sha: str) -> None:
        p = self._path_under(self._remotes, f"{remote}/{branch}")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(sha, encoding="utf-8")

    def delete_remote(self, remote: str, branch: Optional[str] = None) -> None:
        p = self._path_under(
            self._remotes,
            remote if branch is None else f"{remote}/{branch}",
        )
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
            self._prune_empty_parents(p.parent, self._remotes)

        if branch is not None:
            remove_packed_refs(self._root, [f"refs/remotes/{remote}/{branch}"])
        else:
            prefix = f"refs/remotes/{remote}/"
            remove_packed_refs(
                self._root,
                [name for name in list_packed_refnames(self._root, prefix)],
            )

    def rename_remote(self, old: str, new: str) -> None:
        old_path = self._path_under(self._remotes, old)
        new_path = self._path_under(self._remotes, new)
        packed = read_packed_refs(self._root)
        old_prefix = f"refs/remotes/{old}/"
        new_prefix = f"refs/remotes/{new}/"
        packed_old = [name for name in packed if name.startswith(old_prefix)]
        packed_new = [name for name in packed if name.startswith(new_prefix)]

        if not old_path.exists() and not packed_old:
            return
        if new_path.exists() or packed_new:
            raise RuntimeError(f"Remote-tracking namespace already exists: {new}")

        if old_path.exists():
            new_path.parent.mkdir(parents=True, exist_ok=True)
            old_path.rename(new_path)

        if packed_old:
            rewritten = []
            for name, record in packed.items():
                if name.startswith(old_prefix):
                    replacement = new_prefix + name[len(old_prefix) :]
                    rewritten.append(PackedRef(record.oid, replacement, record.peeled_oid))
                else:
                    rewritten.append(record)
            write_packed_refs(self._root, rewritten)

    def list_remotes(self, remote: Optional[str] = None) -> List[str]:
        if remote is None:
            loose = set(self._list_under(self._remotes))
            prefix = "refs/remotes/"
            packed = {
                name[len(prefix) :]
                for name in list_packed_refnames(self._root, prefix)
            }
            return sorted(loose | packed)

        root = self._path_under(self._remotes, remote)
        loose = set(self._list_under(root)) if root.exists() else set()
        prefix = f"refs/remotes/{remote}/"
        packed = {
            name[len(prefix) :]
            for name in list_packed_refnames(self._root, prefix)
        }
        return sorted(loose | packed)

    # ------------------------------------------------------------------
    # Stash ref
    # ------------------------------------------------------------------

    def get_stash(self) -> Optional[str]:
        return self._resolve_refname("refs/stash")

    def set_stash(self, sha: str, message: str = "stash") -> None:
        old_sha = self.get_stash()
        self._stash.parent.mkdir(parents=True, exist_ok=True)
        self._stash.write_text(sha, encoding="utf-8")
        self._append_reflog("refs/stash", old_sha, sha, message)

    def delete_stash(self, message: str = "stash pop") -> None:
        old_sha = self.get_stash()
        if self._stash.exists():
            self._stash.unlink()
        removed = remove_packed_refs(self._root, ["refs/stash"])
        if old_sha is not None and (removed or not self._stash.exists()):
            self._append_reflog("refs/stash", old_sha, None, message)

    # ------------------------------------------------------------------
    # Reflogs
    # ------------------------------------------------------------------

    def read_reflog(self, ref: str = "HEAD") -> List[ReflogEntry]:
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
        if name == "HEAD":
            return self.resolve_head()
        if self._is_oid(name):
            return name.lower()
        if name.startswith("refs/"):
            return self._resolve_refname(name)
        branch = self.get_branch(name)
        if branch:
            return branch
        tag = self.get_tag(name)
        if tag:
            return tag
        if "/" in name:
            remote, branch_name = name.split("/", 1)
            remote_sha = self.get_remote(remote, branch_name)
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
