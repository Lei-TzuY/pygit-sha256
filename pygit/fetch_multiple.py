"""Git-style multi-remote fetch orchestration.

Phase188 adds ``fetch --all``, ``fetch --multiple`` and fetch remote groups on
top of the single-source Phase183-187 fetch stack.  Execution is intentionally
sequential for now: Git permits parallel execution with ``--jobs``, but the
observable ref/FETCH_HEAD semantics do not require pretending pygit has a
parallel transport scheduler.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple

from .config import GitConfig
from .repo import Repository


_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


def _bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in _TRUE:
        return True
    if normalized in _FALSE:
        return False
    raise ValueError(f"invalid boolean configuration value: {value!r}")


def remote_group_members(repo: Repository, name: str) -> Tuple[str, ...] | None:
    """Return ordered members of ``remotes.<name>``, or ``None`` if absent.

    Duplicate members are retained, matching native Git's group expansion.
    Unlike the older push helper, validation is deferred to the ordinary fetch
    of each member so a multi-source fetch can continue past one bad member.
    """
    value = GitConfig(repo.pygit_dir).get("remotes", name)
    if value is None:
        return None
    members = tuple(value.split())
    if not members:
        raise RuntimeError(f"remote group '{name}' has no members")
    return members


def expand_fetch_sources(repo: Repository, names: Sequence[str]) -> List[str]:
    """Expand each remote/group argument in source order, preserving duplicates."""
    expanded: List[str] = []
    for name in names:
        members = remote_group_members(repo, name)
        if members is None:
            expanded.append(name)
        else:
            expanded.extend(members)
    return expanded


def all_fetch_remotes(repo: Repository) -> List[str]:
    """Return configured remotes eligible for ``fetch --all``.

    ``remote.<name>.skipFetchAll`` excludes a remote. Repository insertion order
    is retained so FETCH_HEAD aggregation remains deterministic.
    """
    cfg = GitConfig(repo.pygit_dir)
    result: List[str] = []
    for name in repo.list_remotes():
        if _bool(cfg.get("remote", f"{name}.skipFetchAll"), default=False):
            continue
        result.append(name)
    return result


def fetch_all_by_config(repo: Repository) -> bool:
    """Return ``fetch.all`` with Git's false default."""
    return _bool(GitConfig(repo.pygit_dir).get("fetch", "all"), default=False)


@dataclass(frozen=True)
class MultiFetchResult:
    remote: str
    ok: bool
    error: str | None = None


def run_multi_fetch(
    repo: Repository,
    remotes: Sequence[str],
    fetch_one,
) -> List[MultiFetchResult]:
    """Run each remote fetch sequentially and aggregate failures.

    ``fetch_one(remote, append)`` receives ``append=False`` for the first
    attempted source and true thereafter, causing one logical multi-fetch to
    replace FETCH_HEAD once and then append subsequent sources.  Later remotes
    are attempted even after an earlier failure.
    """
    results: List[MultiFetchResult] = []
    append = False
    for remote in remotes:
        try:
            fetch_one(remote, append)
        except (RuntimeError, ValueError, KeyError, FileNotFoundError, OSError) as exc:
            results.append(MultiFetchResult(remote=remote, ok=False, error=str(exc)))
        else:
            results.append(MultiFetchResult(remote=remote, ok=True))
        append = True
    return results
