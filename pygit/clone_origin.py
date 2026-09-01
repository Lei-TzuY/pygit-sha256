"""Clone-time support for Git's custom ``-o/--origin`` remote name.

The mature clone transports historically materialize a temporary ``origin``
namespace.  This module moves that completed local state to the user-selected
remote without changing transport/object identity semantics.
"""

from __future__ import annotations

import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping, Set, Tuple

from .promisor import read_promisor_state, write_promisor_state
from .ref_transaction import _validate_refname
from .remote_lifecycle import rename_remote
from .repo import Repository


DEFAULT_CLONE_REMOTE = "origin"

# Clone-origin retargeting is a local metadata-finalization step.  Keep its
# rollback surface explicit and deliberately exclude immutable object content,
# HEAD/local branches, the index and the worktree.
_RETARGET_METADATA_ROOTS = (
    "config.json",
    "config",
    "packed-refs",
    "native-map.json",
    "promisor.json",
    "refs/remotes",
    "logs/refs/remotes",
)


@dataclass(frozen=True)
class _RetargetMetadataSnapshot:
    """Exact pre-retarget bytes/modes for the mutable clone-origin surface."""

    directories: Tuple[str, ...]
    files: Mapping[str, Tuple[bytes, int]]


def _metadata_relative(repo: Repository, path: Path) -> str:
    return path.relative_to(repo.pygit_dir).as_posix()


def _snapshot_retarget_metadata(repo: Repository) -> _RetargetMetadataSnapshot:
    """Capture only metadata that clone-origin finalization may mutate.

    Ref/config metadata symlinks are rejected rather than followed.  The clone
    finalizer is not a general filesystem backup mechanism, and following a
    symlink here would turn rollback into an unexpected write outside ``.pygit``.
    """

    directories: Set[str] = set()
    files: Dict[str, Tuple[bytes, int]] = {}

    def capture(path: Path) -> None:
        if path.is_symlink():
            raise RuntimeError(
                f"clone remote retarget refuses symlinked metadata: "
                f"{_metadata_relative(repo, path)}"
            )
        if path.is_file():
            rel = _metadata_relative(repo, path)
            files[rel] = (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
            return
        if not path.exists():
            return
        if not path.is_dir():
            raise RuntimeError(
                f"clone remote retarget encountered unsupported metadata node: "
                f"{_metadata_relative(repo, path)}"
            )

        directories.add(_metadata_relative(repo, path))
        for child in sorted(path.iterdir(), key=lambda item: item.name):
            capture(child)

    for relative in _RETARGET_METADATA_ROOTS:
        capture(repo.pygit_dir / relative)

    return _RetargetMetadataSnapshot(
        directories=tuple(sorted(directories, key=lambda value: (value.count("/"), value))),
        files=dict(files),
    )


def _remove_metadata_root(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _restore_retarget_metadata(
    repo: Repository,
    snapshot: _RetargetMetadataSnapshot,
) -> None:
    """Restore the exact captured clone-origin metadata surface after failure."""

    for relative in _RETARGET_METADATA_ROOTS:
        _remove_metadata_root(repo.pygit_dir / relative)

    for relative in snapshot.directories:
        (repo.pygit_dir / relative).mkdir(parents=True, exist_ok=True)

    for relative, (payload, mode) in sorted(snapshot.files.items()):
        path = repo.pygit_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        os.chmod(path, mode)


def validate_clone_remote_name(name: str) -> str:
    """Return one ref-safe clone remote name or raise ``ValueError``.

    A clone remote becomes a namespace under ``refs/remotes/<name>/...``.  Reuse
    the repository's existing refname validator on a synthetic child ref so
    slashes remain supported while traversal, control characters and other
    ref-invalid spellings fail before any network access.
    """

    if not isinstance(name, str) or not name:
        raise ValueError("clone remote name must be non-empty")
    if any(char.isspace() for char in name) or "\x00" in name:
        raise ValueError("clone remote name must not contain whitespace or NUL")
    _validate_refname(f"refs/remotes/{name}/__pygit_clone_probe__", allow_head=False)
    return name


def _retarget_promisor_state(repo: Repository, old: str, new: str) -> None:
    path = repo.pygit_dir / "promisor.json"
    if not path.is_file():
        return
    state = read_promisor_state(repo.pygit_dir)
    remotes = state.get("remotes")
    if not isinstance(remotes, dict) or old not in remotes:
        return
    if new in remotes:
        raise RuntimeError(f"Promisor remote already exists: '{new}'")
    remotes[new] = remotes.pop(old)
    write_promisor_state(repo.pygit_dir, state)


def retarget_completed_clone_remote(
    repo: Repository,
    remote: str,
    *,
    old: str = DEFAULT_CLONE_REMOTE,
) -> None:
    """Move a completed clone's local remote namespace to ``remote``.

    ``Repository.rename_remote`` already moves legacy config, remote-tracking
    refs and the native-object map, while ``remote_lifecycle.rename_remote`` also
    rewrites Git-style remote/branch config.  Partial clones additionally retain
    a remote key in ``promisor.json`` and an ``extensions.partialClone`` value;
    move those as part of the same observable clone finalization.

    Phase374 makes this multi-file same-process operation failure-atomic: if a
    later metadata write raises, the exact clone-origin metadata snapshot from
    before the rename is restored.  This is intentionally not a cross-process
    crash transaction; the generic remote lifecycle and native Git lock/CAS
    layers remain responsible for concurrent-writer coordination.
    """

    remote = validate_clone_remote_name(remote)
    if remote == old:
        return

    state = (
        read_promisor_state(repo.pygit_dir)
        if (repo.pygit_dir / "promisor.json").is_file()
        else None
    )
    if state is not None:
        remotes = state.get("remotes", {})
        if isinstance(remotes, Mapping) and remote in remotes and remote != old:
            raise RuntimeError(f"Promisor remote already exists: '{remote}'")

    snapshot = _snapshot_retarget_metadata(repo)
    try:
        rename_remote(repo, old, remote)
        _retarget_promisor_state(repo, old, remote)

        if repo.config_get("extensions", "partialClone") == old:
            repo.config_set("extensions", "partialClone", remote)
    except Exception as exc:
        try:
            _restore_retarget_metadata(repo, snapshot)
        except Exception as rollback_exc:
            raise RuntimeError(
                "clone remote retarget failed and metadata rollback also failed: "
                f"{exc}"
            ) from rollback_exc
        raise
