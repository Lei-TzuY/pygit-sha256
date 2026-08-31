"""Fetch transition for clones whose local branch is still unborn.

Phase331 can clone an explicitly empty protocol-v2 remote without fabricating an
object id. Native Git's ``--single-branch`` empty clone intentionally has no
``remote.<name>.fetch`` refspec, yet a later plain ``fetch`` still requests the
unborn branch's configured upstream into FETCH_HEAD once that remote branch is
born. This module supplies that command-scoped source selection while leaving
the persisted empty fetch-refspec state unchanged.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional, Sequence

from . import fetch_configured
from .fetch_partial import run_fetch as _run_fetch
from .fetch_policy import FetchRefspec, parse_fetch_refspec


_TRUE = {"true", "yes", "on", "1"}


def _is_persistent_partial_remote(repo, remote: str) -> bool:
    return (
        repo.config_get("remote", f"{remote}.partialCloneFilter") is not None
        or (repo.config_get("remote", f"{remote}.promisor") or "").strip().lower()
        in _TRUE
    )


def _unborn_upstream_refspec(
    repo,
    remote: str,
    *,
    allow_persistent_partial: bool = False,
) -> Optional[FetchRefspec]:
    """Return the Phase331 unborn upstream as a source-only fetch refspec.

    The fallback is intentionally narrow. It applies only when the current
    branch has no local tip, the branch explicitly tracks *remote*, and the
    historical clone metadata still names that branch as the remote default.
    A configured fetch refspec always wins before this helper is consulted.

    Persistent partial-clone remotes are excluded by default. Native Git
    automatically reuses their configured object filter on later fetches, so an
    ordinary caller must never synthesize this source-only selection and then
    enter an unfiltered transport. Phase352's filter-aware wrapper opts in only
    while the persisted/explicit filter transport is already active.
    """

    branch = repo.refs.current_branch()
    if not branch or repo.refs.get_branch(branch) is not None:
        return None

    if repo.config_get("branch", f"{branch}.remote") != remote:
        return None

    merge = repo.config_get("branch", f"{branch}.merge")
    expected = f"refs/heads/{branch}"
    if merge != expected:
        return None

    historical = repo._read_config().get("remotes", {}).get(remote, {})
    if historical.get("default_branch") != branch:
        return None

    if _is_persistent_partial_remote(repo, remote) and not allow_persistent_partial:
        return None

    # No destination is deliberate: native Git's first fetch after an empty
    # --single-branch clone records the branch in FETCH_HEAD but does not create
    # refs/remotes/<remote>/<branch> and does not resolve the local unborn branch.
    spec = parse_fetch_refspec(merge)
    if spec.destination is not None or spec.negative or "*" in spec.source:
        return None
    return spec


@contextmanager
def unborn_fetch_selection(*, allow_persistent_partial: bool = False) -> Iterator[None]:
    """Install the command-scoped unborn-upstream selection fallback.

    ``allow_persistent_partial`` is a trust-boundary switch, not a convenience
    option. It is intended only for callers that have already selected a real
    filtered protocol-v2 transport. The default preserves Phase335's fail-closed
    protection against accidental unfiltered fetches from promisor remotes.
    """

    original = fetch_configured._parsed_fetch_refspecs

    def parsed(repo, remote: str):
        configured = original(repo, remote)
        if configured:
            return configured
        implicit = _unborn_upstream_refspec(
            repo,
            remote,
            allow_persistent_partial=allow_persistent_partial,
        )
        return [implicit] if implicit is not None else configured

    fetch_configured._parsed_fetch_refspecs = parsed
    try:
        yield
    finally:
        fetch_configured._parsed_fetch_refspecs = original


def run_fetch(argv: Sequence[str]) -> int:
    """Run the established fetch stack with unborn first-fetch selection."""

    with unborn_fetch_selection():
        return _run_fetch(argv)
