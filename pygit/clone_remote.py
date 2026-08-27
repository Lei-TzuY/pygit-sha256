"""Clone-time remote metadata and default-branch finalization.

Repository.clone historically owns the initial transport, while the modern
configuration and symbolic remote-HEAD layers were added later.  This module
bridges those layers after the initial clone without changing the mature
Repository.clone API.
"""

from __future__ import annotations

from typing import Optional

from .repo import Repository


def clone_default_branch(repo: Repository, remote: str = "origin") -> Optional[str]:
    """Return the server default branch remembered by the initial fetch."""
    settings = repo._read_config().get("remotes", {}).get(remote, {})
    value = settings.get("default_branch")
    return str(value) if value else None


def configure_clone_remote(
    repo: Repository,
    url: str,
    checked_out_branch: str,
    *,
    remote: str = "origin",
    default_branch: Optional[str] = None,
    single_branch: bool = False,
) -> None:
    """Materialize Git-style clone config and the remote default-branch alias.

    The initial transport still uses pygit's historical config.json endpoint.
    Once cloning completes, persist the equivalent Git-style URL/fetch mapping,
    prune non-selected tracking branches for single-branch clones, and create
    ``refs/remotes/<remote>/HEAD`` only when its target was actually fetched.
    """
    if not checked_out_branch:
        raise ValueError("clone finalization requires a checked-out branch")

    repo.config_set("remote", f"{remote}.url", url)
    if single_branch:
        fetch_refspec = (
            f"+refs/heads/{checked_out_branch}:"
            f"refs/remotes/{remote}/{checked_out_branch}"
        )
        for branch in list(repo.refs.list_remotes(remote)):
            if branch != checked_out_branch:
                repo.refs.delete_remote(remote, branch)
    else:
        fetch_refspec = f"+refs/heads/*:refs/remotes/{remote}/*"
    repo.config_set("remote", f"{remote}.fetch", fetch_refspec)

    selected_default = default_branch or clone_default_branch(repo, remote)
    if selected_default and repo.refs.get_remote(remote, selected_default) is not None:
        repo.refs.set_remote_head(remote, selected_default)
    else:
        # Native Git omits origin/HEAD for e.g. --single-branch -b dev when the
        # server's real default branch was not fetched into the clone.
        repo.refs.delete_remote_head(remote)
