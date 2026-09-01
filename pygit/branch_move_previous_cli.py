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
    """Move pygit's flattened ``branch.<name>.*`` configuration keys.

    Native Git permits a destination config section to pre-exist even when the
    destination ref does not. Git's config format can retain duplicate values;
    pygit's scalar config API cannot. Preserve the already-visible destination
    value for a colliding key and move every non-colliding source key.
    """

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


def _move_branch_ref(repo: Repository, old: str, new: str) -> None:
    """Move one local branch while preserving native-style reflog history."""

    old_oid = repo.refs.get_branch(old)
    if old_oid is None:
        raise ValueError(f"no branch named {old!r}")
    if repo.refs.get_branch(new) is not None:
        raise ValueError(f"a branch named {new!r} already exists")

    old_ref = f"refs/heads/{old}"
    new_ref = f"refs/heads/{new}"
    old_log = repo.refs._log_path(old_ref)
    new_log = repo.refs._log_path(new_ref)
    old_log_bytes = old_log.read_bytes() if old_log.exists() else b""
    current = repo.refs.current_branch() == old
    message = f"Branch: renamed {old_ref} to {new_ref}"

    # Materialize the destination first so HEAD never points at a missing ref.
    repo.refs.set_branch(new, old_oid, message=f"branch: created {new}")
    if current:
        repo.refs.set_head_symbolic(new, message=message)
    repo.refs.delete_branch(old)

    # ``set_branch`` necessarily created a synthetic destination reflog entry.
    # Replace it with the source history, then append Git's same-OID rename event.
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
    """Capture exact file bytes/existence for focused rename rollback."""

    snapshots: dict[Path, Optional[bytes]] = {}
    for path in paths:
        snapshots[path] = path.read_bytes() if path.is_file() else None
    return snapshots


def _restore_paths(snapshots: Mapping[Path, Optional[bytes]]) -> None:
    """Restore the pre-rename file state after any partial mutation failure."""

    for path, content in snapshots.items():
        if content is None:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _move_branch_atomically(repo: Repository, old: str, new: str) -> None:
    """Move config/ref/reflog/HEAD state as one fail-closed focused operation."""

    snapshots = _snapshot_paths(_rename_mutation_paths(repo, old, new))
    try:
        _move_branch_config(repo, old, new)
        _move_branch_ref(repo, old, new)
    except Exception:
        _restore_paths(snapshots)
        raise


def run_branch_move_previous(argv: Sequence[str]) -> int:
    """Handle ``branch -m/--move @{-N} <new>`` exactly."""

    parser = argparse.ArgumentParser(
        prog="pygit branch",
        description="Rename a branch selected from previous checkout history.",
    )
    parser.add_argument("-m", "--move", action="store_true", required=True)
    parser.add_argument("old", metavar="@{-N}")
    parser.add_argument("new", metavar="NEW-BRANCH")
    args = parser.parse_args(list(argv))

    repo = _find_repo()
    expanded = expand_previous_checkout(repo, args.old)
    if expanded is None:
        raise ValueError(f"{args.old!r} is not a previous checkout selector")

    # A detached previous destination expands to a genuine local SHA-256 OID,
    # which is a valid revision but not a branch operand for ``git branch -m``.
    if repo.refs.get_branch(expanded) is None:
        raise ValueError(f"no branch named {args.old!r}")

    new_name = check_ref_format(args.new, branch=True)
    if expanded == new_name or repo.refs.get_branch(new_name) is not None:
        raise ValueError(f"a branch named {new_name!r} already exists")

    _move_branch_atomically(repo, expanded, new_name)
    return 0
