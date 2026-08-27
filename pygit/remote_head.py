"""Git-style remote default-branch symbolic-reference helpers."""

from __future__ import annotations

from typing import Optional

from .remote import SmartHttpClient
from .remote_urls import fetch_url
from .repo import Repository


def remote_head_target(repo: Repository, remote: str) -> Optional[str]:
    """Return the fully-qualified target of ``refs/remotes/<remote>/HEAD``."""
    branch = repo.refs.get_remote_head(remote)
    if branch is None:
        return None
    return f"refs/remotes/{remote}/{branch}"


def _write_remote_head(repo: Repository, remote: str, branch: str) -> None:
    if not branch or branch.startswith(("/", "\\")):
        raise ValueError(f"invalid remote HEAD branch: {branch!r}")
    if repo.refs.get_remote(remote, branch) is None:
        raise RuntimeError(f"Not a valid ref: refs/remotes/{remote}/{branch}")
    repo.refs.set_remote_head(remote, branch)


def _discover_default_branch(repo: Repository, remote: str) -> str:
    advertisement = SmartHttpClient(fetch_url(repo, remote)).discover()
    target = advertisement.symrefs.get("HEAD")
    if target and target.startswith("refs/heads/"):
        branch = target[len("refs/heads/") :]
    else:
        branch = repo._infer_default_branch(advertisement.refs)
    if not branch:
        raise RuntimeError(f"Cannot determine remote HEAD for '{remote}'")
    return branch


def set_remote_head(
    repo: Repository,
    remote: str,
    branch: Optional[str] = None,
    *,
    auto: bool = False,
    delete: bool = False,
) -> Optional[str]:
    """Set, auto-detect, or delete a named remote's tracking ``HEAD`` symref.

    Explicit set/delete operations are ref-oriented like native Git: they do
    not require a corresponding remote configuration entry. ``--auto`` does
    query the configured fetch URL. The selected tracking branch must already
    exist before a symbolic remote HEAD can point at it.
    """
    if auto and delete:
        raise ValueError("--auto and --delete are mutually exclusive")
    if branch is not None and (auto or delete):
        raise ValueError("a branch cannot be combined with --auto or --delete")

    if delete:
        repo.refs.delete_remote_head(remote)
        return None

    selected = _discover_default_branch(repo, remote) if auto else branch
    if selected is None:
        raise ValueError("remote set-head requires --auto, --delete, or a branch")
    _write_remote_head(repo, remote, selected)
    return selected
