"""Branch upstream tracking helpers shared by clone/branch/checkout CLIs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .repo import Repository


@dataclass(frozen=True)
class TrackingSource:
    """One branch-like start point that can act as an upstream."""

    remote: str
    branch: str
    oid: str

    @property
    def display(self) -> str:
        return self.branch if self.remote == "." else f"{self.remote}/{self.branch}"


def find_repo() -> Repository:
    for candidate in [Path.cwd(), *Path.cwd().parents]:
        if (candidate / ".pygit").is_dir():
            return Repository(str(candidate))
    raise RuntimeError(
        "fatal: not a pygit repository (or any of the parent directories): .pygit"
    )


def _strip_remote_prefix(name: str) -> str:
    if name.startswith("refs/remotes/"):
        return name[len("refs/remotes/") :]
    if name.startswith("remotes/"):
        return name[len("remotes/") :]
    return name


def remote_tracking_source(repo: Repository, name: str) -> Optional[TrackingSource]:
    candidate = _strip_remote_prefix(name)
    if "/" not in candidate:
        return None
    remote, branch = candidate.split("/", 1)
    if not remote or not branch:
        return None
    oid = repo.refs.get_remote(remote, branch)
    if not oid:
        return None
    return TrackingSource(remote=remote, branch=branch, oid=oid)


def local_tracking_source(repo: Repository, name: str) -> Optional[TrackingSource]:
    candidate = name
    if candidate.startswith("refs/heads/"):
        candidate = candidate[len("refs/heads/") :]
    oid = repo.refs.get_branch(candidate)
    if not oid:
        return None
    return TrackingSource(remote=".", branch=candidate, oid=oid)


def resolve_tracking_source(repo: Repository, name: str) -> Optional[TrackingSource]:
    return remote_tracking_source(repo, name) or local_tracking_source(repo, name)


def set_branch_upstream(repo: Repository, branch: str, source: TrackingSource) -> None:
    repo.config_set("branch", f"{branch}.remote", source.remote)
    repo.config_set("branch", f"{branch}.merge", f"refs/heads/{source.branch}")


def unset_branch_upstream(repo: Repository, branch: str) -> None:
    repo.config_unset("branch", f"{branch}.remote")
    repo.config_unset("branch", f"{branch}.merge")


def copy_branch_upstream(repo: Repository, source_branch: str, new_branch: str) -> bool:
    remote = repo.config_get("branch", f"{source_branch}.remote")
    merge = repo.config_get("branch", f"{source_branch}.merge")
    if not remote or not merge:
        return False
    repo.config_set("branch", f"{new_branch}.remote", remote)
    repo.config_set("branch", f"{new_branch}.merge", merge)
    return True


def move_branch_upstream(repo: Repository, old_branch: str, new_branch: str) -> None:
    remote = repo.config_get("branch", f"{old_branch}.remote")
    merge = repo.config_get("branch", f"{old_branch}.merge")
    if remote is not None:
        repo.config_set("branch", f"{new_branch}.remote", remote)
    if merge is not None:
        repo.config_set("branch", f"{new_branch}.merge", merge)
    unset_branch_upstream(repo, old_branch)


def _auto_setup_mode(repo: Repository) -> str:
    raw = repo.config_get("branch", "autoSetupMerge")
    if raw is None:
        return "true"
    token = raw.strip().lower()
    if token in {"true", "yes", "on", "1"}:
        return "true"
    if token in {"false", "no", "off", "0"}:
        return "false"
    if token in {"always", "inherit", "simple"}:
        return token
    raise ValueError(f"invalid branch.autoSetupMerge value: {raw!r}")


def configure_new_branch_tracking(
    repo: Repository,
    new_branch: str,
    start_point: str,
    *,
    track: Optional[str] = None,
    no_track: bool = False,
) -> Optional[str]:
    """Apply Git-style direct/inherit/auto upstream setup for a new branch."""
    if no_track:
        return None

    if track == "inherit":
        source_branch = start_point
        if source_branch.startswith("refs/heads/"):
            source_branch = source_branch[len("refs/heads/") :]
        if not repo.refs.get_branch(source_branch):
            raise RuntimeError("--track=inherit requires a local branch start-point")
        if not copy_branch_upstream(repo, source_branch, new_branch):
            raise RuntimeError(
                f"cannot inherit upstream configuration from '{source_branch}'"
            )
        return "inherit"

    source = resolve_tracking_source(repo, start_point)
    if track == "direct":
        if source is None:
            raise RuntimeError(
                f"cannot set up tracking: '{start_point}' is not a branch"
            )
        set_branch_upstream(repo, new_branch, source)
        return source.display

    mode = _auto_setup_mode(repo)
    if mode == "false":
        return None
    if mode == "inherit":
        source_branch = start_point
        if source_branch.startswith("refs/heads/"):
            source_branch = source_branch[len("refs/heads/") :]
        if repo.refs.get_branch(source_branch) and copy_branch_upstream(
            repo, source_branch, new_branch
        ):
            return "inherit"
        return None
    if source is None:
        return None
    if mode == "true" and source.remote == ".":
        return None
    if mode == "simple":
        if source.remote == "." or new_branch != source.branch:
            return None
    set_branch_upstream(repo, new_branch, source)
    return source.display


def configure_clone_tracking(
    repo: Repository,
    branch: str,
    *,
    remote: str = "origin",
) -> None:
    oid = repo.refs.get_remote(remote, branch)
    if not oid:
        raise RuntimeError(f"remote branch not found: '{remote}/{branch}'")
    set_branch_upstream(repo, branch, TrackingSource(remote, branch, oid))


def remote_candidates(repo: Repository, branch: str) -> List[TrackingSource]:
    candidates: List[TrackingSource] = []
    for refname in repo.refs.list_remotes():
        if "/" not in refname:
            continue
        remote, remote_branch = refname.split("/", 1)
        if remote_branch != branch:
            continue
        oid = repo.refs.get_remote(remote, remote_branch)
        if oid:
            candidates.append(TrackingSource(remote, remote_branch, oid))
    return sorted(candidates, key=lambda item: (item.remote, item.branch))


def choose_remote_candidate(repo: Repository, branch: str) -> Optional[TrackingSource]:
    candidates = remote_candidates(repo, branch)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    preferred = repo.config_get("checkout", "defaultRemote")
    if preferred:
        preferred_matches = [c for c in candidates if c.remote == preferred]
        if len(preferred_matches) == 1:
            return preferred_matches[0]
    names = ", ".join(candidate.display for candidate in candidates)
    raise RuntimeError(
        f"branch '{branch}' matches multiple remote-tracking branches: {names}"
    )
