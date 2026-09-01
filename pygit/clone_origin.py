"""Clone-time support for Git's custom ``-o/--origin`` remote name.

The mature clone transports historically materialize a temporary ``origin``
namespace.  This module moves that completed local state to the user-selected
remote without changing transport/object identity semantics.
"""

from __future__ import annotations

from typing import Mapping

from .promisor import read_promisor_state, write_promisor_state
from .ref_transaction import _validate_refname
from .remote_lifecycle import rename_remote
from .repo import Repository


DEFAULT_CLONE_REMOTE = "origin"


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
    """

    remote = validate_clone_remote_name(remote)
    if remote == old:
        return

    state = read_promisor_state(repo.pygit_dir) if (repo.pygit_dir / "promisor.json").is_file() else None
    if state is not None:
        remotes = state.get("remotes", {})
        if isinstance(remotes, Mapping) and remote in remotes and remote != old:
            raise RuntimeError(f"Promisor remote already exists: '{remote}'")

    rename_remote(repo, old, remote)
    _retarget_promisor_state(repo, old, remote)

    if repo.config_get("extensions", "partialClone") == old:
        repo.config_set("extensions", "partialClone", remote)
