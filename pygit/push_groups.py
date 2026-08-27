"""Git-style push remote-group helpers.

A remote group is configured as ``remotes.<name>`` and expands to a
whitespace-separated sequence of named remotes.  Group pushes intentionally
reuse the ordinary per-remote push planner/transport so every member evaluates
its own ``remote.<name>.push`` and ``remote.<name>.mirror`` settings.
"""

from __future__ import annotations

from typing import Optional, Tuple

from .config import GitConfig
from .repo import Repository


def remote_group_members(repo: Repository, name: Optional[str]) -> Optional[Tuple[str, ...]]:
    """Return configured members for *name*, or ``None`` when it is not a group.

    Git stores a group in the ``[remotes]`` section as a whitespace-separated
    list.  An explicitly configured but empty group is rejected rather than
    falling through and being mistaken for a remote name.
    """
    if not name:
        return None

    value = GitConfig(repo.pygit_dir).get("remotes", name)
    if value is None:
        return None

    members = tuple(value.split())
    if not members:
        raise RuntimeError(f"remote group '{name}' has no members")

    configured = repo.list_remotes()
    unknown = [member for member in members if member not in configured]
    if unknown:
        joined = ", ".join(unknown)
        raise RuntimeError(f"remote group '{name}' contains unknown remote(s): {joined}")
    return members
