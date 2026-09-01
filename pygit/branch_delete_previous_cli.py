"""Focused ``branch -d/-D`` support for previous-checkout selectors."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence, Tuple

from .branch_checkout import expand_previous_checkout
from .branch_move_previous_cli import _branch_storage_path, _restore_paths, _snapshot_paths
from .entrypoint import _find_repo
from .plumbing import is_ancestor
from .repo import Repository


def _parse_delete_args(argv: Sequence[str]) -> Tuple[bool, str]:
    args = list(argv)
    if len(args) == 2 and args[0] in {"-d", "--delete", "-D"}:
        return args[0] == "-D", args[1]
    if len(args) == 3:
        options = args[:2]
        delete_options = [item for item in options if item in {"-d", "--delete"}]
        force_options = [item for item in options if item in {"-f", "--force"}]
        if len(delete_options) == 1 and len(force_options) == 1:
            return True, args[2]
    raise ValueError("unsupported previous-checkout branch-delete argument shape")


def _configured_upstream(repo: Repository, branch: str) -> str | None:
    remote = repo.config_get("branch", f"{branch}.remote")
    merge = repo.config_get("branch", f"{branch}.merge")
    if not remote or not merge:
        return None
    if remote == ".":
        candidate = merge
    elif merge.startswith("refs/heads/"):
        candidate = f"{remote}/{merge[len('refs/heads/'):]}"
    else:
        candidate = merge
    return candidate if repo.refs.resolve(candidate) is not None else None


def _delete_mutation_paths(repo: Repository, branch: str) -> tuple[Path, ...]:
    return (
        repo.pygit_dir / "config",
        repo.pygit_dir / "packed-refs",
        _branch_storage_path(repo, branch),
        repo.refs._log_path(f"refs/heads/{branch}"),
    )


def _remove_branch_config(repo: Repository, branch: str) -> None:
    prefix = f"{branch}."
    keys = [
        key
        for section, key, _value in repo.config_list()
        if section == "branch" and key.startswith(prefix)
    ]
    for key in keys:
        repo.config_unset("branch", key)


def _delete_branch_atomically(repo: Repository, branch: str) -> None:
    snapshots = _snapshot_paths(_delete_mutation_paths(repo, branch))
    try:
        repo.refs.delete_branch(branch)
        log_path = repo.refs._log_path(f"refs/heads/{branch}")
        if log_path.exists():
            log_path.unlink()
        _remove_branch_config(repo, branch)
    except Exception:
        _restore_paths(snapshots)
        raise


def run_branch_delete_previous(argv: Sequence[str]) -> int:
    """Delete the branch selected by ``@{-N}`` with Git-compatible safety."""

    force, selector = _parse_delete_args(argv)
    repo = _find_repo()
    expanded = expand_previous_checkout(repo, selector)
    if expanded is None:
        raise ValueError(f"{selector!r} is not a previous checkout selector")

    source_oid = repo.refs.get_branch(expanded)
    if source_oid is None:
        raise ValueError(f"no branch named {selector!r}")
    if repo.refs.current_branch() == expanded:
        raise ValueError(f"cannot delete branch {expanded!r} used by the current worktree")

    if not force:
        upstream = _configured_upstream(repo, expanded)
        target = upstream or "HEAD"
        target_oid = repo.refs.resolve(target)
        if target_oid is None:
            raise RuntimeError("cannot verify whether branch is fully merged")
        if not is_ancestor(repo, source_oid, target_oid):
            raise RuntimeError(
                f"branch {expanded!r} is not fully merged; use -D to force deletion"
            )
        head_oid = repo.refs.resolve_head()
        if upstream is not None and head_oid is not None and not is_ancestor(repo, source_oid, head_oid):
            print(
                f"warning: deleting branch {expanded!r} that has been merged to {upstream!r}, "
                "but not yet merged to HEAD",
                file=__import__("sys").stderr,
            )

    _delete_branch_atomically(repo, expanded)
    print(f"Deleted branch {expanded} (was {source_oid[:7]}).")
    return 0
