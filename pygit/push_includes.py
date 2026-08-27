"""Git-style ``push --force-if-includes`` safety checks.

The option is intentionally modeled as an ancillary guard for implicit
``--force-with-lease`` expectations.  It protects against background fetches
that advance a remote-tracking branch after the user last integrated that
remote state into the local branch's reflog history.
"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

from .push_lease import LeasePolicy
from .repo import Repository


_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}
_ZERO_INTERNAL_OID = "0" * 64


def extract_force_if_includes(argv: Sequence[str]) -> Tuple[Tuple[str, ...], Optional[bool]]:
    """Remove ``--[no-]force-if-includes`` while preserving last-option wins.

    ``None`` means the command line did not override configuration.
    """
    cleaned = []
    override: Optional[bool] = None
    for token in argv:
        if token == "--force-if-includes":
            override = True
            continue
        if token == "--no-force-if-includes":
            override = False
            continue
        cleaned.append(token)
    return tuple(cleaned), override


def configured_force_if_includes(repo: Repository) -> bool:
    """Return ``push.useForceIfIncludes`` with Git-style boolean spelling."""
    value = repo.config_get("push", "useForceIfIncludes")
    if value is None:
        return False
    normalized = value.strip().lower()
    if normalized in _TRUE:
        return True
    if normalized in _FALSE:
        return False
    raise RuntimeError(f"invalid boolean value for push.useForceIfIncludes: '{value}'")


def resolve_force_if_includes(repo: Repository, cli_override: Optional[bool]) -> bool:
    """Resolve command-line precedence over ``push.useForceIfIncludes``."""
    if cli_override is not None:
        return cli_override
    return configured_force_if_includes(repo)


def _implicit_lease_applies(lease: Optional[LeasePolicy], target_ref: str) -> bool:
    """Return whether force-if-includes is meaningful for *target_ref*.

    Git documents this guard as a no-op when no force-with-lease is active or
    when the effective lease supplies an exact ``:<expect>`` value.
    """
    if lease is None or not lease.active:
        return False
    request = lease.request_for(target_ref)
    return request is not None and not request.explicit_expect


def require_force_if_includes(
    enabled: bool,
    lease: Optional[LeasePolicy],
    repo: Repository,
    remote: str,
    target_ref: str,
) -> bool:
    """Verify that an implicitly leased remote-tracking tip was integrated.

    The remote-tracking branch for ``refs/heads/<name>`` is compared with the
    reflog of the *local branch having the destination name*.  This matches
    native Git behavior for pushes such as ``topic:main`` and for deletions:
    the proof is attached to the branch based on ``origin/main``, not merely to
    the source ref selected for this particular push.

    A missing remote-tracking tip means there is no fetched remote state to
    integrate, so the includes guard has nothing additional to prove.  When a
    tracking tip exists, at least one recorded local-branch reflog tip must be a
    descendant of (or equal to) that tracking tip.
    """
    if not enabled or not _implicit_lease_applies(lease, target_ref):
        return False
    if not target_ref.startswith("refs/heads/"):
        return False

    branch = target_ref[len("refs/heads/") :]
    tracking_tip = repo.refs.get_remote(remote, branch)
    if not tracking_tip:
        return False

    # Git's check is tied to the local branch based on the remote-tracking ref.
    # A differently named source branch does not substitute for a missing local
    # destination branch.
    if not repo.refs.get_branch(branch):
        raise RuntimeError(
            f"Push rejected: remote ref updated since checkout for '{target_ref}'."
        )

    for entry in repo.refs.read_reflog(f"refs/heads/{branch}"):
        tip = entry.new_sha
        if not tip or tip == _ZERO_INTERNAL_OID:
            continue
        try:
            ancestors = repo._ancestor_distances(tip)
        except (KeyError, ValueError):
            # A stale/corrupt reflog entry cannot establish the safety proof.
            continue
        if tracking_tip in ancestors:
            return True

    raise RuntimeError(
        f"Push rejected: remote ref updated since checkout for '{target_ref}'."
    )
