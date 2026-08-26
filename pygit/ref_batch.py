"""Partial-success reference batches for ``update-ref --batch-updates``.

Git's batch-updates mode keeps protocol/parsing failures fatal, while ref
transaction conflicts caused by incorrect user expectations may reject one
update without discarding unrelated valid updates.  This module layers that
policy over pygit's existing mixed direct/symbolic transaction planner.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

from .ref_transaction import RefUpdate, _apply_updates, _plan_updates
from .repo import Repository
from .refs import ZERO_SHA


@dataclass(frozen=True)
class RefRejection:
    """One update rejected by a partial-success reference batch."""

    refname: str
    new_value: str
    old_value: str
    reason: str

    def format(self) -> str:
        return f"rejected {self.refname} {self.new_value} {self.old_value} {self.reason}"


def _rejection_reason(update: RefUpdate, exc: BaseException) -> Optional[str]:
    """Map pygit transaction conflicts to Git-style rejectable categories.

    Syntax/protocol errors, invalid object expressions, invalid refnames, branch
    type violations, and I/O failures deliberately remain fatal.  Only errors
    corresponding to ref-transaction conflicts are downgraded to per-update
    rejections.
    """

    text = str(exc)
    if isinstance(exc, RuntimeError):
        if "reference already exists" in text:
            return "reference already exists"
        if "expected symbolic target" in text or "is not a symbolic ref" in text:
            return "expected symref"
        if "cannot lock ref" in text and "expected" in text:
            if update.action in {"create", "symref-create"}:
                return "reference already exists"
            return "incorrect old value provided"
        if "symbolic-ref transaction would create a cycle" in text:
            return "name conflict"
    if isinstance(exc, ValueError) and "multiple updates for the same ref" in text:
        return "name conflict"
    return None


def _display_values(update: RefUpdate) -> tuple[str, str]:
    if update.action.startswith("symref-"):
        new_value = update.new_target or ZERO_SHA
        if update.old_kind == "oid":
            old_value = update.old_oid or ZERO_SHA
        else:
            old_value = update.old_target or ZERO_SHA
        return new_value, old_value

    new_value = update.new_oid or ZERO_SHA
    old_value = update.old_oid or ZERO_SHA
    return new_value, old_value


def _with_deref(update: RefUpdate, deref: bool) -> RefUpdate:
    return RefUpdate(
        action=update.action,
        refname=update.refname,
        new_oid=update.new_oid,
        old_oid=update.old_oid,
        deref=deref,
        new_target=update.new_target,
        old_target=update.old_target,
        old_kind=update.old_kind,
    )


def _try_queue(
    repo: Repository,
    pending: Sequence[RefUpdate],
    update: RefUpdate,
    *,
    deref: bool,
) -> Optional[RefRejection]:
    try:
        _plan_updates(repo, [*pending, update], deref=deref)
    except (RuntimeError, ValueError) as exc:
        reason = _rejection_reason(update, exc)
        if reason is None:
            raise
        new_value, old_value = _display_values(update)
        return RefRejection(update.refname, new_value, old_value, reason)
    return None


def update_refs_batch(
    repo: Repository,
    updates: Sequence[RefUpdate],
    *,
    message: str = "update-ref",
    deref: bool = True,
) -> List[RefRejection]:
    """Execute ``update-ref --batch-updates`` with partial-success semantics.

    Rejectable ref-state conflicts are removed from the active transaction and
    returned for reporting.  All surviving updates are still published through
    the ordinary transaction publisher, so an OSError or other system failure
    remains fatal and rolls the surviving batch back rather than becoming a
    per-ref rejection.

    Explicit ``start``/``prepare``/``commit``/``abort`` sessions retain Phase
    98 semantics. Rejections belong to their transaction: commit reports them,
    abort or an explicit transaction reaching EOF discards them.
    """

    pending: List[RefUpdate] = []
    transaction_rejections: List[RefRejection] = []
    emitted: List[RefRejection] = []
    explicit = False
    prepared = False
    next_no_deref = False

    for update in updates:
        action = update.action

        if action == "option":
            if update.refname != "no-deref":
                raise ValueError(f"unsupported update-ref option: {update.refname!r}")
            if prepared:
                raise RuntimeError("prepared transactions can only be closed")
            next_no_deref = True
            continue

        if action == "start":
            if prepared:
                raise RuntimeError("prepared transactions can only be closed")
            if explicit:
                raise RuntimeError("transaction already started")
            explicit = True
            continue

        if action == "prepare":
            if prepared:
                raise RuntimeError("transaction already prepared")
            _plan_updates(repo, pending, deref=deref)
            prepared = True
            explicit = True
            continue

        if action == "commit":
            _apply_updates(repo, pending, message=message, deref=deref)
            emitted.extend(transaction_rejections)
            pending.clear()
            transaction_rejections.clear()
            explicit = False
            prepared = False
            next_no_deref = False
            continue

        if action == "abort":
            pending.clear()
            transaction_rejections.clear()
            explicit = False
            prepared = False
            next_no_deref = False
            continue

        if prepared:
            raise RuntimeError("prepared transactions can only be closed")

        effective_deref = False if next_no_deref else deref
        next_no_deref = False
        candidate = _with_deref(update, effective_deref)
        rejection = _try_queue(repo, pending, candidate, deref=deref)
        if rejection is not None:
            transaction_rejections.append(rejection)
            continue
        pending.append(candidate)

    if explicit or prepared:
        return emitted

    if pending:
        _apply_updates(repo, pending, message=message, deref=deref)
    emitted.extend(transaction_rejections)
    return emitted
