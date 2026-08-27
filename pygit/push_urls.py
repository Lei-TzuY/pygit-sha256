"""Resolve Git-style remote push URLs and scope one destination per push pass.

Git permits a remote to have multiple ``pushurl`` values.  If none are
configured, every configured ``url`` is a push destination; the repository's
legacy JSON remote URL remains the compatibility fallback.

The push stack predates multi-URL remotes and consistently obtains its endpoint
through ``Repository._read_config()``.  ``use_push_url`` therefore provides a
scoped, in-memory view of that same config with only the selected destination
URL replaced.  Nothing is written to disk, and all existing planner/transport
APIs continue to work unchanged.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from typing import Iterator, Tuple

from .config import GitConfig
from .repo import Repository


def _legacy_url(repo: Repository, remote: str) -> str:
    config = repo._read_config()
    settings = config.get("remotes", {}).get(remote)
    if not settings:
        raise KeyError(f"Unknown remote: '{remote}'")
    value = str(settings.get("url") or "").strip()
    if not value:
        raise RuntimeError(f"Remote '{remote}' has no URL configured")
    return value


def _values(repo: Repository, remote: str, key: str) -> Tuple[str, ...]:
    values = GitConfig(repo.pygit_dir).get_all("remote", f"{remote}.{key}")
    return tuple(value for value in (item.strip() for item in values) if value)


def remote_push_urls(repo: Repository, remote: str) -> Tuple[str, ...]:
    """Return ordered push destinations for *remote*.

    ``remote.<name>.pushurl`` replaces ordinary URLs when present.  Otherwise
    all ``remote.<name>.url`` values are push destinations.  Repositories made
    through pygit's historical ``remote add`` store one URL in config.json, so
    that value is retained as the final compatibility fallback.
    """
    legacy = _legacy_url(repo, remote)  # validates the named remote first
    push_urls = _values(repo, remote, "pushurl")
    if push_urls:
        return push_urls
    urls = _values(repo, remote, "url")
    if urls:
        return urls
    return (legacy,)


@contextmanager
def use_push_url(repo: Repository, remote: str, url: str) -> Iterator[None]:
    """Temporarily expose *url* as the remote's legacy URL to push code.

    The override is instance-local and never persists config.json.  It lets the
    mature push planners and transports operate on one destination at a time
    without introducing URL parameters throughout their public APIs.
    """
    original = repo._read_config

    def read_with_override():
        config = deepcopy(original())
        settings = config.get("remotes", {}).get(remote)
        if not settings:
            raise KeyError(f"Unknown remote: '{remote}'")
        settings["url"] = url
        return config

    repo._read_config = read_with_override  # type: ignore[method-assign]
    try:
        yield
    finally:
        # ``original`` is the bound method that existed before this scope.  It
        # may itself be an outer override when contexts are nested.
        repo._read_config = original  # type: ignore[method-assign]
