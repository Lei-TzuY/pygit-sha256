"""First-pull transition for a branch created by an empty remote clone.

Phase331 can leave local ``HEAD`` symbolically attached to an unborn branch and
Phase335 can later fetch the remote's first concrete branch without resolving
that local branch.  Native Git's next ``pull`` promotes the fetched commit to the
local branch and checks it out with an ``initial pull`` reflog entry.

This module owns that narrow transition.  Generic merge deliberately keeps its
historical "cannot merge into an empty repository" contract.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

from .fetch_porcelain import fetch_porcelain
from .fetch_unborn_transition import unborn_fetch_selection
from .objects import CommitObject
from .remote_ops import Upstream


class UnbornPullBootstrapError(RuntimeError):
    """Raised when an unborn first pull cannot be applied safely."""


def _persistent_partial_clone(repo, remote: str) -> bool:
    filter_spec = repo.config_get("remote", f"{remote}.partialCloneFilter")
    promisor = (repo.config_get("remote", f"{remote}.promisor") or "").strip().lower()
    return filter_spec is not None or promisor in {"true", "yes", "on", "1"}


def _current_unborn_branch(repo) -> Optional[str]:
    branch = repo.refs.current_branch()
    if not branch:
        return None
    if repo.refs.resolve_head() is not None or repo.refs.get_branch(branch) is not None:
        return None
    return branch


def _target_paths(repo, target_sha: str) -> set[str]:
    commit = repo.store.read(target_sha)
    if not isinstance(commit, CommitObject):
        raise UnbornPullBootstrapError("first pull target is not a commit")
    return set(repo._commit_tree_entries(target_sha))


def _path_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _worktree_conflicts(repo, target_paths: set[str]) -> list[str]:
    """Return untracked filesystem paths that a first checkout would overwrite."""

    conflicts: set[str] = set()
    for relative in target_paths:
        path = repo.worktree / relative
        if _path_exists(path):
            conflicts.add(relative)
            continue

        # A file or symlink in an ancestor position prevents safely creating a
        # directory needed by the target tree (for example local ``dir`` vs
        # target ``dir/file``).  A symlink-to-directory is still a conflict: the
        # checkout must never follow it outside the repository worktree.
        parent = path.parent
        while parent != repo.worktree:
            if parent.is_symlink() or (_path_exists(parent) and not parent.is_dir()):
                conflicts.add(parent.relative_to(repo.worktree).as_posix())
                break
            parent = parent.parent
    return sorted(conflicts)


def _preflight_local_transition(repo, target_sha: str) -> None:
    """Validate local mutable state before changing worktree/index/branch."""

    # Native Git can preserve some non-conflicting staged additions, but pygit's
    # established checkout primitive rebuilds the index.  Refuse rather than
    # silently discard user staging; a later phase can implement a true index
    # three-way transition.
    staged = list(repo.index.paths())
    if staged:
        rendered = ", ".join(sorted(staged))
        raise UnbornPullBootstrapError(
            f"cannot perform initial pull with staged index entries: {rendered}"
        )

    conflicts = _worktree_conflicts(repo, _target_paths(repo, target_sha))
    if conflicts:
        rendered = "\n\t".join(conflicts)
        raise UnbornPullBootstrapError(
            "untracked working tree files would be overwritten by initial pull:\n\t"
            + rendered
        )


def _fetched_upstream_oid(result: Dict[str, object], source: Upstream) -> str:
    refs = result.get("refs")
    if not isinstance(refs, dict):
        raise UnbornPullBootstrapError("first pull fetch returned malformed ref metadata")
    refname = f"refs/heads/{source.branch}"
    value = refs.get(refname)
    if not isinstance(value, str) or len(value) != 64:
        raise KeyError(f"Remote branch not found: '{source.display}'")
    try:
        int(value, 16)
    except ValueError as exc:
        raise UnbornPullBootstrapError(
            "first pull fetch returned a malformed local SHA-256 identity"
        ) from exc
    return value.lower()


def try_pull_unborn_upstream(repo, source: Upstream) -> Optional[Dict[str, object]]:
    """Perform the first pull into an unborn local branch, or return ``None``.

    The transition is intentionally limited to the branch/upstream relationship
    created by Phase331.  Fetch occurs first and may update FETCH_HEAD or a
    normal remote-tracking ref.  The local branch remains unborn until all
    checkout safety checks pass.
    """

    branch = _current_unborn_branch(repo)
    if branch is None or source.remote == ".":
        return None
    if source.branch != branch:
        return None

    if repo.config_get("branch", f"{branch}.remote") != source.remote:
        return None
    if repo.config_get("branch", f"{branch}.merge") != f"refs/heads/{branch}":
        return None

    if _persistent_partial_clone(repo, source.remote):
        raise UnbornPullBootstrapError(
            "initial pull for an empty partial clone requires filtered fetch support"
        )

    # Phase335 supplies a source-only refspec for an empty --single-branch clone
    # without mutating the intentionally absent persistent fetch refspec.
    with unborn_fetch_selection():
        fetched = fetch_porcelain(repo, source.remote)

    target_sha = _fetched_upstream_oid(fetched, source)

    # Fetch is allowed to publish remote/FETCH_HEAD state first, matching native
    # Git.  Do not resolve the local branch until checkout can be completed.
    if _current_unborn_branch(repo) != branch:
        raise UnbornPullBootstrapError(
            "local branch changed while preparing the initial pull"
        )
    _preflight_local_transition(repo, target_sha)

    repo._replace_worktree_from_commit(target_sha)
    repo.refs.set_branch(branch, target_sha, message="initial pull")

    return {
        "status": "initial-pull",
        "sha": target_sha,
        "conflicts": [],
        "fetch": fetched,
    }
