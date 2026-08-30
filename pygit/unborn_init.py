"""Initialize a pristine pygit repository from protocol-v2 unborn HEAD metadata.

An ``unborn`` ls-refs record describes reference state, not an object.  This
module is the narrow bridge between Phase315's transport metadata and local
repository initialization.  It deliberately does not fetch, import, translate,
or manufacture an object ID.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from .protocol_v2_unborn import ProtocolV2LsRefsResult

if TYPE_CHECKING:  # pragma: no cover - import cycle avoidance for type checking only
    from .repo import Repository


class EmptyRemoteInitializationError(RuntimeError):
    """Raised when remote unborn metadata cannot safely initialize local HEAD."""


_INVALID_REF_CHARS = frozenset(" ~^:?*[\\")


def _validate_unborn_head_target(target: str) -> str:
    """Return the branch part of a safe ``refs/heads/...`` symref target."""

    prefix = "refs/heads/"
    if not target.startswith(prefix):
        raise EmptyRemoteInitializationError(
            "unborn HEAD symref-target must name refs/heads/<branch>"
        )

    branch = target[len(prefix) :]
    components = branch.split("/")
    if (
        not branch
        or branch == "@"
        or branch.endswith(("/", "."))
        or "//" in branch
        or ".." in branch
        or "@{" in branch
        or any(
            not component
            or component.startswith(".")
            or component.endswith((".lock", "."))
            for component in components
        )
        or any(
            ord(char) < 32
            or ord(char) == 127
            or char in _INVALID_REF_CHARS
            for char in branch
        )
    ):
        raise EmptyRemoteInitializationError(
            f"invalid unborn HEAD branch target: {target!r}"
        )
    return branch


def _object_database_has_files(objects_dir: Path) -> bool:
    """Return whether the local object database contains any file state."""

    return any(path.is_file() for path in objects_dir.rglob("*"))


def _write_head_atomically(head_path: Path, target: str) -> None:
    """Publish one symbolic HEAD without creating a reflog or object identity."""

    lock_path = head_path.with_name(f"{head_path.name}.lock")
    try:
        fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    except FileExistsError as exc:
        raise EmptyRemoteInitializationError(
            f"cannot initialize unborn HEAD: lock already exists: {lock_path.name}"
        ) from exc

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(f"ref: {target}")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(lock_path, head_path)
    finally:
        if lock_path.exists():
            lock_path.unlink()


def initialize_empty_remote_head(
    repo: "Repository",
    result: ProtocolV2LsRefsResult,
) -> str:
    """Initialize local symbolic HEAD from an explicit empty-remote result.

    The function accepts only the narrow native empty-repository shape: an
    explicitly unborn ``HEAD``, no concrete remote refs, and a branch symref
    target.  The destination must still be pristine: no resolved HEAD, local
    refs, local object files, or promisor metadata.  After every check succeeds,
    only ``.pygit/HEAD`` is replaced.  The target branch ref intentionally
    remains absent, which is what makes HEAD unborn.

    Returns the local branch name (for example ``"main"`` or ``"topic/x"``).
    """

    if result.unborn != frozenset({"HEAD"}):
        raise EmptyRemoteInitializationError(
            "empty-remote initialization requires explicit unborn HEAD metadata"
        )

    advertisement = result.advertisement
    if "HEAD" in advertisement.refs:
        raise EmptyRemoteInitializationError(
            "remote HEAD cannot be both concrete and unborn"
        )
    if advertisement.refs:
        raise EmptyRemoteInitializationError(
            "empty-remote initialization cannot consume concrete remote refs"
        )

    target = advertisement.symrefs.get("HEAD")
    if not target:
        raise EmptyRemoteInitializationError(
            "unborn HEAD is missing symref-target metadata"
        )
    if set(advertisement.symrefs) != {"HEAD"}:
        raise EmptyRemoteInitializationError(
            "empty-remote initialization received unexpected symbolic refs"
        )
    branch = _validate_unborn_head_target(target)

    # Every repository-state validation is deliberately completed before the
    # first mutation below.  In particular, resolving HEAD and enumerating refs
    # are read-only and must not materialize promised objects.
    if repo.refs.resolve_head() is not None:
        raise EmptyRemoteInitializationError(
            "cannot initialize unborn HEAD in a repository with a resolved HEAD"
        )
    if repo.refs.list_branches() or repo.refs.list_tags() or repo.refs.list_remotes():
        raise EmptyRemoteInitializationError(
            "cannot initialize unborn HEAD in a repository with existing refs"
        )
    if _object_database_has_files(repo.store.root):
        raise EmptyRemoteInitializationError(
            "cannot initialize unborn HEAD in a repository with local object state"
        )
    if (repo.pygit_dir / "promisor.json").exists():
        raise EmptyRemoteInitializationError(
            "cannot initialize unborn HEAD in a repository with promisor state"
        )

    current = repo.refs.get_head()
    if not current.startswith("ref: refs/heads/"):
        raise EmptyRemoteInitializationError(
            "cannot replace a detached or malformed local HEAD with unborn HEAD"
        )

    desired = f"ref: {target}"
    if current == desired:
        return branch

    _write_head_atomically(repo.pygit_dir / "HEAD", target)
    return branch
