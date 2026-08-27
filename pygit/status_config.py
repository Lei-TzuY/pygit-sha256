"""Git-style configuration resolution for :mod:`pygit.status_cli`.

Phase 162 centralises status presentation defaults that are controlled by
repository configuration while preserving command-line precedence.  Keeping
this logic separate from the status renderers makes porcelain/short/long mode
rules explicit and leaves ``Repository.status()`` unchanged.
"""

from __future__ import annotations

from typing import Optional

from .repo import Repository

_TRUE_VALUES = {"true", "yes", "on", "1"}
_FALSE_VALUES = {"false", "no", "off", "0"}


def _parse_bool(value: str, *, key: str) -> bool:
    token = value.strip().lower()
    if token in _TRUE_VALUES:
        return True
    if token in _FALSE_VALUES:
        return False
    raise ValueError(f"invalid {key} boolean value: {value!r}")


def configured_show_stash(repo: Repository) -> bool:
    """Return ``status.showStash`` with Git's false default."""
    value = repo.config_get("status", "showstash")
    if value is None:
        return False
    return _parse_bool(value, key="status.showStash")


def resolve_show_stash(repo: Repository, cli_value: Optional[bool]) -> bool:
    """Apply ``--[no-]show-stash`` over ``status.showStash``."""
    if cli_value is not None:
        return cli_value
    return configured_show_stash(repo)


def configured_untracked_mode(repo: Repository) -> str:
    """Return ``status.showUntrackedFiles`` using Git's ``normal`` default."""
    value = repo.config_get("status", "showuntrackedfiles")
    if value is None:
        return "normal"
    token = value.strip().lower()
    if token in {"no", "normal", "all"}:
        return token
    if token in _TRUE_VALUES:
        return "normal"
    if token in _FALSE_VALUES:
        return "no"
    raise ValueError(f"invalid status.showUntrackedFiles value: {value!r}")


def resolve_untracked_mode(repo: Repository, cli_value: Optional[str]) -> str:
    """Apply ``-u/--untracked-files`` over the configured default."""
    if cli_value is not None:
        return cli_value
    return configured_untracked_mode(repo)


def configured_ahead_behind(repo: Repository) -> bool:
    """Return ``status.aheadBehind`` using Git's true default."""
    value = repo.config_get("status", "aheadbehind")
    if value is None:
        return True
    return _parse_bool(value, key="status.aheadBehind")


def resolve_ahead_behind(
    repo: Repository,
    cli_value: Optional[bool],
    *,
    porcelain: bool,
) -> bool:
    """Resolve detailed ahead/behind reporting.

    Git documents ``status.aheadBehind`` as a default only for non-porcelain
    formats.  Porcelain v1/v2 therefore ignore the config value, while explicit
    ``--ahead-behind`` / ``--no-ahead-behind`` still take precedence.
    """
    if cli_value is not None:
        return cli_value
    if porcelain:
        return True
    return configured_ahead_behind(repo)
