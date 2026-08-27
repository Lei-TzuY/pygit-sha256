"""Resolve Git-style default remotes and upstreams for pull/push."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .repo import Repository


@dataclass(frozen=True)
class Upstream:
    """Configured upstream identity for one local branch."""

    remote: str
    branch: str

    @property
    def display(self) -> str:
        return self.branch if self.remote == "." else f"{self.remote}/{self.branch}"


def configured_upstream(repo: Repository, branch: Optional[str] = None) -> Optional[Upstream]:
    """Return branch.<name>.remote/merge, rejecting partial configuration."""
    local = branch or repo.refs.current_branch()
    if not local:
        raise RuntimeError("cannot resolve an upstream from detached HEAD")

    remote = repo.config_get("branch", f"{local}.remote")
    merge = repo.config_get("branch", f"{local}.merge")
    if remote is None and merge is None:
        return None
    if not remote or not merge:
        raise RuntimeError(f"branch '{local}' has incomplete upstream configuration")
    if not merge.startswith("refs/heads/"):
        raise RuntimeError(
            f"branch '{local}' has unsupported upstream merge ref '{merge}'"
        )
    upstream_branch = merge[len("refs/heads/") :]
    if not upstream_branch:
        raise RuntimeError(f"branch '{local}' has an empty upstream branch")
    return Upstream(remote=remote, branch=upstream_branch)


def resolve_pull_source(
    repo: Repository,
    remote: Optional[str] = None,
    branch: Optional[str] = None,
) -> Upstream:
    """Resolve the repository/refspec pair used by a pull invocation."""
    current = repo.refs.current_branch()
    if not current:
        raise RuntimeError("cannot pull with a detached HEAD")

    if branch is not None:
        return Upstream(remote=remote or "origin", branch=branch)

    upstream = configured_upstream(repo, current)
    if remote is None:
        if upstream is not None:
            return upstream
        return Upstream(remote="origin", branch=current)

    if upstream is not None and upstream.remote == remote:
        return upstream
    return Upstream(remote=remote, branch=current)


def resolve_push_remote(repo: Repository, explicit: Optional[str] = None) -> str:
    """Resolve Git's branch/global push-remote precedence."""
    if explicit:
        return explicit

    branch = repo.refs.current_branch()
    if not branch:
        raise RuntimeError("cannot push from detached HEAD")

    branch_push = repo.config_get("branch", f"{branch}.pushRemote")
    if branch_push:
        return branch_push

    global_push = repo.config_get("remote", "pushDefault")
    if global_push:
        return global_push

    upstream = configured_upstream(repo, branch)
    if upstream is not None and upstream.remote != ".":
        return upstream.remote

    remotes = repo.list_remotes()
    if "origin" in remotes:
        return "origin"
    if len(remotes) == 1:
        return next(iter(remotes))
    if not remotes:
        raise RuntimeError("no configured push remote")
    raise RuntimeError("no default push remote; specify one explicitly")
