"""Resolve Git-style previous-checkout shorthands and perform checkout navigation."""

from __future__ import annotations

import re
from typing import Optional

from .repo import Repository

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

    ``None`` means *value* is not previous-checkout syntax.  Invalid or
    unavailable selectors raise ``ValueError`` so callers can fail closed like
    ``git check-ref-format --branch``.

    Modern/native-style reflog messages containing ``moving from X to Y`` are
    authoritative.  Pygit's older ``moving to Y`` records are also supported:
    the previous checkout destination is used when it identifies the old HEAD,
    otherwise the old OID is mapped to a unique local branch or retained as a
    detached OID.
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

    # Legacy pygit checkout records only stored the destination.  The next
    # older checkout destination is the best symbolic source when it still
    # names the entry's old OID.
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


def checkout_previous(repo: Repository, value: str = "@{-1}") -> str:
    """Checkout a Git-style previous-checkout selector and return its expansion.

    This is the operation-level counterpart to :func:`expand_previous_checkout`.
    Only ``@{-N}`` input is accepted; ordinary revision selection remains the
    responsibility of :meth:`Repository.checkout`.

    The selector is expanded *before* invoking ``Repository.checkout``.  That
    means the resulting HEAD reflog records the concrete branch name or detached
    SHA-256 object ID, matching native Git's observable behavior instead of
    persisting the literal ``@{-N}`` token.
    """

    expanded = expand_previous_checkout(repo, value)
    if expanded is None:
        raise ValueError(f"{value!r} is not a previous checkout selector")
    repo.checkout(expanded)
    return expanded
