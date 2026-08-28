"""
pygit/objects/commit.py
=======================
A **commit** object ties together:
  - a root **tree** hash  (the full directory snapshot)
  - zero or more **parent** commit hashes  (the commit's lineage)
  - **author** and **committer** identity + timestamp
  - a free-form **message**

Commit objects form a DAG (Directed Acyclic Graph) — this is the commit
history.  Every commit is immutable once written; changing even a single
character in the message produces a completely different hash, which is
why Git's history cannot be silently tampered with.

On-disk format (the payload that follows the outer header):

    tree <tree-sha>\\n
    parent <sha>\\n          (zero or more; absent for root commits)
    author <name> <email> <unix-ts> <tz>\\n
    committer <name> <email> <unix-ts> <tz>\\n
    \\n
    <message>

Phase204 also permits imported commits to store their original native Git
parents as ``parent-sha1 <40-hex>`` headers.  Those headers are part of the
content-addressed local object, so a shallow commit keeps the same SHA-256 id
when its omitted parents arrive later.  ``parents`` remains the runtime list of
resolved local SHA-256 parent ids and is populated by ObjectStore.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional
import time
from .base import GitObject


@dataclass
class Identity:
    """Author or committer identity."""
    name:      str
    email:     str
    timestamp: int  = field(default_factory=lambda: int(time.time()))
    timezone:  str  = "+0000"

    def encode(self) -> str:
        return f"{self.name} <{self.email}> {self.timestamp} {self.timezone}"

    @classmethod
    def decode(cls, s: str) -> "Identity":
        # Format: "Name <email> timestamp tz"
        tz   = s.rsplit(" ", 1)[-1]
        rest = s.rsplit(" ", 1)[0]
        ts   = int(rest.rsplit(" ", 1)[-1])
        rest = rest.rsplit(" ", 1)[0]
        gt   = rest.rindex(">")
        lt   = rest.rindex("<")
        email = rest[lt + 1:gt]
        name  = rest[:lt].strip()
        return cls(name=name, email=email, timestamp=ts, timezone=tz)


class CommitObject(GitObject):
    """
    A commit snapshot.

    Attributes
    ----------
    tree           : SHA-256 hex of the root TreeObject
    parents        : resolved local SHA-256 parent commit ids
    native_parents : original native SHA-1 parents for stable foreign commits,
                     or ``None`` for ordinary pygit commits
    author         : :class:`Identity`
    committer      : :class:`Identity`
    message        : commit message (may be multi-line)
    """

    type_name = b"commit"

    def __init__(
        self,
        tree:      str      = "",
        parents:   List[str] | None = None,
        author:    Identity | None = None,
        committer: Identity | None = None,
        message:   str      = "",
        native_parents: List[str] | None = None,
    ) -> None:
        self.tree      = tree
        self.parents:  List[str] = parents or []
        self.native_parents: List[str] | None = (
            None if native_parents is None else list(native_parents)
        )
        self.author    = author    or Identity("Unknown", "unknown@example.com")
        self.committer = committer or self.author
        self.message   = message

    # ------------------------------------------------------------------
    # GitObject protocol
    # ------------------------------------------------------------------

    def serialize(self) -> bytes:
        lines: List[str] = []
        lines.append(f"tree {self.tree}")
        if self.native_parents is None:
            for p in self.parents:
                lines.append(f"parent {p}")
        else:
            for p in self.native_parents:
                lines.append(f"parent-sha1 {p}")
        lines.append(f"author {self.author.encode()}")
        lines.append(f"committer {self.committer.encode()}")
        lines.append("")           # blank line separates headers from body
        lines.append(self.message)
        return "\n".join(lines).encode()

    def deserialize(self, data: bytes) -> None:
        text = data.decode()
        header_block, _, body = text.partition("\n\n")
        self.message = body
        self.parents = []
        native_parents: List[str] = []
        saw_native_parent = False
        for line in header_block.splitlines():
            if line.startswith("tree "):
                self.tree = line[5:]
            elif line.startswith("parent "):
                self.parents.append(line[7:])
            elif line.startswith("parent-sha1 "):
                native_parents.append(line[12:])
                saw_native_parent = True
            elif line.startswith("author "):
                self.author = Identity.decode(line[7:])
            elif line.startswith("committer "):
                self.committer = Identity.decode(line[10:])
        self.native_parents = native_parents if saw_native_parent else None

    # ------------------------------------------------------------------
    # Pretty-print (used by 'log')
    # ------------------------------------------------------------------

    def pretty_print(self, sha: str, date_format: Optional[str] = None) -> str:
        import datetime
        dt = datetime.datetime.fromtimestamp(
            self.author.timestamp, tz=datetime.timezone.utc
        )
        if date_format == "short":
            date_str = dt.strftime("%Y-%m-%d")
        elif date_format == "iso":
            date_str = dt.strftime("%Y-%m-%d %H:%M:%S %z")
        elif date_format == "relative":
            now = datetime.datetime.now(tz=datetime.timezone.utc)
            diff = now - dt
            secs = max(0, int(diff.total_seconds()))
            if secs < 60:
                date_str = f"{secs} seconds ago"
            elif secs < 3600:
                date_str = f"{secs // 60} minutes ago"
            elif secs < 86400:
                date_str = f"{secs // 3600} hours ago"
            else:
                date_str = f"{secs // 86400} days ago"
        else:
            date_str = dt.strftime("%a %b %d %H:%M:%S %Y %z")
        lines = [
            f"commit {sha}",
            f"Author: {self.author.name} <{self.author.email}>",
            f"Date:   {date_str}",
            "",
            f"    {self.message}",
        ]
        return "\n".join(lines)
