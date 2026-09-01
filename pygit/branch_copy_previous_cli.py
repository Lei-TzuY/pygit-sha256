"""Focused ``branch -c/-C`` support for previous-checkout selectors."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Sequence, Tuple

from .branch_checkout import expand_previous_checkout
from .branch_move_previous_cli import _branch_storage_path, _restore_paths, _snapshot_paths
from .entrypoint import _find_repo
from .ref_query import check_ref_format
from .repo import Repository


def _parse_copy_args(argv: Sequence[str]) -> Tuple[bool, str, str]:
    """Return ``(force, source_selector, destination)`` for the focused grammar."""

    args = list(argv)
    if len(args) == 3 and args[0] in {"-c", "--copy", "-C"}:
        return args[0] == "-C", args[1], args[2]

    if len(args) == 4:
        options = args[:2]
        copy_options = [item for item in options if item in {"-c", "--copy"}]
        force_options = [item for item in options if item in {"-f", "--force"}]
        if len(copy_options) == 1 and len(force_options) == 1:
            return True, args[2], args[3]

    raise ValueError("unsupported previous-checkout branch-copy argument shape")


def _copy_branch_config(repo, source: str, destination: str) -> None:
    """Copy source branch config while preserving existing destination overrides.

    Native ``git branch -C`` copies the source branch section in front of an
    already-existing destination section. Consequently destination keys that
    already existed remain the effective values, while source-only keys become
    available on the copied branch. Pygit's config backend stores one value per
    key, so retaining an existing destination value reproduces that observable
    precedence without inventing a multi-valued representation.
    """

    source_prefix = f"{source}."
    destination_prefix = f"{destination}."
    entries = list(repo.config_list())
    destination_keys = {
        key
        for section, key, _value in entries
        if section == "branch" and key.startswith(destination_prefix)
    }
    source_entries: Dict[str, str] = {
        key[len(source_prefix) :]: value
        for section, key, value in entries
        if section == "branch" and key.startswith(source_prefix)
    }
    for suffix, value in source_entries.items():
        target_key = destination_prefix + suffix
        if target_key not in destination_keys:
            repo.config_set("branch", target_key, value)


def _copy_mutation_paths(repo: Repository, destination: str) -> tuple[Path, ...]:
    """Return every mutable file whose bytes may change during branch copy."""

    return (
        repo.pygit_dir / "config",
        repo.pygit_dir / "packed-refs",
        _branch_storage_path(repo, destination),
        repo.refs._log_path(f"refs/heads/{destination}"),
    )


def _copy_branch_atomically(
    repo: Repository,
    source: str,
    destination: str,
    source_oid: str,
) -> None:
    """Copy ref/reflog/config state and restore exact pre-copy bytes on failure."""

    snapshots = _snapshot_paths(_copy_mutation_paths(repo, destination))
    try:
        # Capture the source log before set_branch() in the source==destination case.
        source_log = repo.refs._log_path(f"refs/heads/{source}")
        source_history = source_log.read_bytes() if source_log.exists() else b""

        repo.refs.set_branch(
            destination,
            source_oid,
            message=f"Branch: copied refs/heads/{source} to refs/heads/{destination}",
        )

        # set_branch() updates the ref correctly but its normal movement event is
        # not Git branch-copy history. Replace the destination log with the source
        # log, then append Git's forced same-OID copy event.
        destination_log = repo.refs._log_path(f"refs/heads/{destination}")
        destination_log.parent.mkdir(parents=True, exist_ok=True)
        if source_history:
            destination_log.write_bytes(source_history)
        elif destination_log.exists():
            destination_log.unlink()
        repo.refs._append_reflog(
            f"refs/heads/{destination}",
            source_oid,
            source_oid,
            f"Branch: copied refs/heads/{source} to refs/heads/{destination}",
            force=True,
        )

        _copy_branch_config(repo, source, destination)
    except Exception:
        _restore_paths(snapshots)
        raise


def run_branch_copy_previous(argv: Sequence[str]) -> int:
    """Handle exact ``branch -c/-C @{-N} <new>`` forms.

    ``branch -c``/``--copy`` copies a *branch*, not an arbitrary revision. A
    previous-checkout selector that resolves to a detached commit therefore
    fails even though that commit is otherwise a valid branch start point.
    """

    force, selector, destination = _parse_copy_args(argv)
    repo = _find_repo()

    expanded = expand_previous_checkout(repo, selector)
    if expanded is None:
        raise ValueError(f"{selector!r} is not a previous checkout selector")

    source_oid = repo.refs.get_branch(expanded)
    if source_oid is None:
        raise ValueError(f"no branch named {selector!r}")

    check_ref_format(f"refs/heads/{destination}")
    existing = repo.refs.get_branch(destination)

    # Native Git treats copying a branch onto itself as a successful reflog-only
    # copy operation, including when that branch is checked out.
    same_branch = destination == expanded
    if existing is not None and not same_branch:
        if not force:
            raise ValueError(f"a branch named {destination!r} already exists")
        if repo.refs.current_branch() == destination:
            raise ValueError(
                f"cannot force update the branch {destination!r} used by the current worktree"
            )

    _copy_branch_atomically(repo, expanded, destination, source_oid)
    return 0
