"""Resolve Git-style remote push URLs and scope one destination per push pass."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from typing import Iterator, Tuple

from .remote_urls import push_urls
from .repo import Repository


def remote_push_urls(repo: Repository, remote: str) -> Tuple[str, ...]:
    """Compatibility wrapper returning every Git-style push destination."""
    return push_urls(repo, remote)


@contextmanager
def use_push_url(repo: Repository, remote: str, url: str) -> Iterator[None]:
    """Temporarily expose *url* as the remote's legacy URL to push code.

    The override is instance-local and never persists config.json. It lets the
    mature push planners and transports operate on one destination at a time
    without introducing URL parameters throughout their APIs.
    """
    had_instance_override = "_read_config" in repo.__dict__
    previous_instance_value = repo.__dict__.get("_read_config")
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
        if had_instance_override:
            repo.__dict__["_read_config"] = previous_instance_value
        else:
            repo.__dict__.pop("_read_config", None)
