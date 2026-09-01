"""Resolve Git-style previous-checkout shorthands and perform checkout navigation."""

from __future__ import annotations

import re
from typing import Dict, Optional

from .hooks import HookRunner
from .index import IndexEntry, _mode_for
from .objects import CommitObject
from .repo import Repository
from .sparse import SparseCheckout

_PREVIOUS_CHECKOUT_RE = re.compile(r"^@\{-(\d+)\}$")
_MOVING_FROM_RE = re.compile(r"^checkout: moving from (.+) to (.+)$")
_MOVING_TO_PREFIX = "checkout: moving to "
_ZERO_OID = "0" * 64


def _checkout_destination(message: str) -> Optional[str]:
    native = _MOVING_FROM_RE.match(message)
    if native:
        return native.group(2)
    if message.startswith(_MOVING_TO_PREFIX):
        return message[len(_MOVING_TO_PREFIX) :]
    return None


def _branch_for_oid(repo: Repository, oid: str) -> Optional[str]:
    matches = [
        branch
        for branch in repo.refs.list_branches()
        if repo.refs.get_branch(branch) == oid
    ]
    return matches[0] if len(matches) == 1 else None


def expand_previous_checkout(repo: Repository, value: str) -> Optional[str]:
    """Expand ``@{-N}`` using the HEAD checkout history.

    ``None`` means *value* is not previous-checkout syntax. Invalid or
    unavailable selectors raise ``ValueError`` so callers fail closed like
    ``git check-ref-format --branch``.
    """

    match = _PREVIOUS_CHECKOUT_RE.fullmatch(value)
    if match is None:
        return None

    index = int(match.group(1), 10)
    if index <= 0:
        raise ValueError(f"{value!r} is not a valid previous checkout selector")

    checkout_entries = [
        entry
        for entry in repo.reflog("HEAD")
        if _MOVING_FROM_RE.match(entry.message)
        or entry.message.startswith(_MOVING_TO_PREFIX)
    ]
    if index > len(checkout_entries):
        raise ValueError(f"{value!r} does not name an earlier checkout")

    entry = checkout_entries[index - 1]
    native = _MOVING_FROM_RE.match(entry.message)
    if native:
        source = native.group(1)
        if not source:
            raise ValueError(f"malformed checkout reflog entry for {value!r}")
        return source

    if index < len(checkout_entries):
        older_destination = _checkout_destination(checkout_entries[index].message)
        if older_destination:
            branch_oid = repo.refs.get_branch(older_destination)
            if branch_oid == entry.old_sha:
                return older_destination

    branch = _branch_for_oid(repo, entry.old_sha)
    if branch is not None:
        return branch
    if entry.old_sha != _ZERO_OID:
        return entry.old_sha

    raise ValueError(f"{value!r} does not name an earlier checkout")


def _checkout_detached(repo: Repository, target: str, reflog_target: str) -> None:
    """Restore *target* while leaving HEAD detached and naming *reflog_target*.

    The restore/index path intentionally mirrors :meth:`Repository.checkout`.
    Native Git treats a detached commit switch as a checkout operation for the
    post-checkout hook, so the hook's third argument is ``1`` even though HEAD
    ends detached.
    """

    sha = repo.refs.resolve(target)
    if not sha:
        raise KeyError(f"Unknown revision: '{target}'")

    obj = repo.store.read(sha)
    if not isinstance(obj, CommitObject):
        raise ValueError(f"'{target}' does not point to a commit")

    new_tree: Dict[str, str] = {}
    repo._flatten_tree(obj.tree, "", new_tree)
    sparse = SparseCheckout(repo.pygit_dir)

    for path in set(repo.index.paths()):
        if path not in new_tree or not sparse.matches(path):
            abs_path = repo.worktree / path
            if abs_path.exists():
                abs_path.unlink()

    repo._restore_tree_sparse(obj.tree, repo.worktree, "", sparse)

    repo.index.entries.clear()
    for path, blob_sha in new_tree.items():
        if not sparse.matches(path):
            continue
        abs_path = repo.worktree / path
        mode = _mode_for(abs_path) if abs_path.exists() else "100644"
        stat = abs_path.stat() if abs_path.exists() else None
        repo.index.entries[path] = IndexEntry(
            path=path,
            sha=blob_sha,
            mode=mode,
            size=stat.st_size if stat else 0,
            mtime=stat.st_mtime if stat else 0.0,
        )
    repo.index.save()

    old_sha = repo.refs.resolve_head() or _ZERO_OID
    repo.refs.set_head_detached(
        sha,
        message=f"checkout: moving to {reflog_target}",
    )
    HookRunner(repo.pygit_dir).run_hook("post-checkout", [old_sha, sha, "1"])


def checkout_previous(
    repo: Repository, value: str = "@{-1}", *, detach: bool = False
) -> str:
    """Checkout a Git-style previous-checkout selector and return its expansion."""

    expanded = expand_previous_checkout(repo, value)
    if expanded is None:
        raise ValueError(f"{value!r} is not a previous checkout selector")

    if detach:
        _checkout_detached(repo, expanded, expanded)
    else:
        repo.checkout(expanded)
    return expanded
