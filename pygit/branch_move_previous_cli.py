"""Focused CLI adapter for moving a branch selected by previous checkout history."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Mapping, Optional, Sequence

from .branch_checkout import expand_previous_checkout
from .entrypoint import _find_repo
from .ref_query import check_ref_format
from .repo import Repository


def _move_branch_config(repo: Repository, old: str, new: str) -> None:
    """Move pygit's flattened ``branch.<name>.*`` configuration keys."""
    if old == new:
        return
    old_prefix = f"{old}."
    entries = [
        (key, value)
        for section, key, value in repo.config_list()
        if section == "branch" and key.startswith(old_prefix)
    ]
    for key, value in entries:
        suffix = key[len(old_prefix) :]
        destination_key = f"{new}.{suffix}"
        if repo.config_get("branch", destination_key) is None:
            repo.config_set("branch", destination_key, value)
    for key, _ in entries:
        repo.config_unset("branch", key)


def _move_branch_ref(repo: Repository, old: str, new: str, *, force: bool = False) -> None:
    """Move one local branch while preserving native-style reflog history."""
    old_oid = repo.refs.get_branch(old)
    if old_oid is None:
        raise ValueError(f"no branch named {old!r}")
    destination_oid = repo.refs.get_branch(new)
    current_branch = repo.refs.current_branch()
    if destination_oid is not None and not force:
        raise ValueError(f"a branch named {new!r} already exists")
    if force and current_branch == new:
        raise ValueError(f"cannot force update the checked-out branch {new!r}")

    old_ref = f"refs/heads/{old}"
    new_ref = f"refs/heads/{new}"
    old_log = repo.refs._log_path(old_ref)
    new_log = repo.refs._log_path(new_ref)
    old_log_bytes = old_log.read_bytes() if old_log.exists() else b""
    source_current = current_branch == old
    message = f"Branch: renamed {old_ref} to {new_ref}"

    if old == new:
        repo.refs._append_reflog(new_ref, old_oid, old_oid, message, force=True)
        return

    if force and destination_oid is not None:
        # Drop both loose and packed destination identity. Its reflog is replaced
        # below by the source history, matching native forced branch rename.
        repo.refs.delete_branch(new)

    repo.refs.set_branch(new, old_oid, message=f"branch: created {new}")
    if source_current:
        repo.refs.set_head_symbolic(new, message=message)
    repo.refs.delete_branch(old)

    new_log.parent.mkdir(parents=True, exist_ok=True)
    if old_log_bytes:
        new_log.write_bytes(old_log_bytes)
    elif new_log.exists():
        new_log.unlink()
    repo.refs._append_reflog(new_ref, old_oid, old_oid, message, force=True)

    if old_log.exists():
        old_log.unlink()
        parent = old_log.parent
        stop = repo.pygit_dir / "logs" / "refs" / "heads"
        while parent != stop and parent.exists():
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent


def _branch_storage_path(repo: Repository, name: str) -> Path:
    return repo.pygit_dir / "refs" / "heads" / Path(*name.split("/"))


def _rename_mutation_paths(repo: Repository, old: str, new: str) -> tuple[Path, ...]:
    return (
        repo.pygit_dir / "config",
        repo.pygit_dir / "packed-refs",
        repo.pygit_dir / "HEAD",
        repo.pygit_dir / "logs" / "HEAD",
        _branch_storage_path(repo, old),
        _branch_storage_path(repo, new),
        repo.refs._log_path(f"refs/heads/{old}"),
        repo.refs._log_path(f"refs/heads/{new}"),
    )


def _snapshot_paths(paths: Sequence[Path]) -> dict[Path, Optional[bytes]]:
    snapshots: dict[Path, Optional[bytes]] = {}
    for path in paths:
        snapshots[path] = path.read_bytes() if path.is_file() else None
    return snapshots


def _restore_paths(snapshots: Mapping[Path, Optional[bytes]]) -> None:
    for path, content in snapshots.items():
        if content is None:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _move_branch_atomically(repo: Repository, old: str, new: str, *, force: bool = False) -> None:
    """Move config/ref/reflog/HEAD state as one fail-closed focused operation."""
    snapshots = _snapshot_paths(_rename_mutation_paths(repo, old, new))
    try:
        _move_branch_config(repo, old, new)
        _move_branch_ref(repo, old, new, force=force)
    except Exception:
        _restore_paths(snapshots)
        raise


def run_branch_move_previous(argv: Sequence[str]) -> int:
    """Handle previous-selector ``branch -m`` and force-move ``-M`` forms."""
    parser = argparse.ArgumentParser(
        prog="pygit branch",
        description="Rename a branch selected from previous checkout history.",
    )
    parser.add_argument("-m", "--move", action="store_true")
    parser.add_argument("-M", dest="force_move", action="store_true")
    parser.add_argument("-f", "--force", action="store_true")
    parser.add_argument("old", metavar="@{-N}")
    parser.add_argument("new", metavar="NEW-BRANCH")
    args = parser.parse_args(list(argv))
    if not args.move and not args.force_move:
        raise ValueError("branch move requires -m/--move or -M")
    if args.force and not args.move:
        raise ValueError("--force requires --move in this focused branch form")
    force = bool(args.force_move or args.force)

    repo = _find_repo()
    expanded = expand_previous_checkout(repo, args.old)
    if expanded is None:
        raise ValueError(f"{args.old!r} is not a previous checkout selector")
    if repo.refs.get_branch(expanded) is None:
        raise ValueError(f"no branch named {args.old!r}")

    new_name = check_ref_format(args.new, branch=True)
    destination_oid = repo.refs.get_branch(new_name)
    if not force and (expanded == new_name or destination_oid is not None):
        raise ValueError(f"a branch named {new_name!r} already exists")
    if force and destination_oid is not None and repo.refs.current_branch() == new_name:
        raise ValueError(f"cannot force update the checked-out branch {new_name!r}")

    _move_branch_atomically(repo, expanded, new_name, force=force)
    return 0
