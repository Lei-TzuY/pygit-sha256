"""Filtered first-pull transition for an empty partial clone.

Phase337 owns the ordinary unborn first-pull transition.  Phase352 extends that
narrow lifecycle to repositories created by Phase331 with a persisted partial
clone filter: the first fetch must reuse that filter, then checkout may
materialize only the promised blobs required by the selected worktree.
"""

from __future__ import annotations

from typing import Dict, Optional

from .clone_partial import _collect_checkout_promises
from .fetch_partial import partial_filter_transport
from .fetch_persisted_partial import persisted_partial_filter
from .fetch_porcelain import fetch_porcelain
from .fetch_server_option_config import configured_server_options
from .fetch_unborn_transition import unborn_fetch_selection
from .promisor_materialize import materialize_promised_objects
from .pull_unborn_transition import (
    UnbornPullBootstrapError,
    _current_unborn_branch,
    _fetched_upstream_oid,
    _persistent_partial_clone,
    _preflight_local_transition,
    try_pull_unborn_upstream as _ordinary_try_pull_unborn_upstream,
)
from .remote_ops import Upstream


def _partial_unborn_candidate(repo, source: Upstream) -> Optional[str]:
    """Return the validated persisted filter for one unborn partial upstream.

    ``None`` means that Phase337 should decide whether the ordinary transition
    applies.  A persistent promisor marker without a concrete filter is fail
    closed: pygit cannot safely infer what content the remote is allowed to omit.
    """

    branch = _current_unborn_branch(repo)
    if branch is None or source.remote == "." or source.branch != branch:
        return None
    if repo.config_get("branch", f"{branch}.remote") != source.remote:
        return None
    if repo.config_get("branch", f"{branch}.merge") != f"refs/heads/{branch}":
        return None
    if not _persistent_partial_clone(repo, source.remote):
        return None

    try:
        filter_spec = persisted_partial_filter(repo, source.remote)
    except (RuntimeError, ValueError) as exc:
        raise UnbornPullBootstrapError(
            f"initial pull has invalid persisted partial-clone filter: {exc}"
        ) from exc
    if filter_spec is None:
        raise UnbornPullBootstrapError(
            "initial pull cannot infer a filter for this promisor remote"
        )
    return filter_spec


def try_pull_unborn_upstream(repo, source: Upstream) -> Optional[Dict[str, object]]:
    """Perform the ordinary or filtered first pull for an unborn local branch.

    Non-partial repositories delegate byte-for-byte to Phase337.  A persisted
    partial clone reuses the recorded filter in the existing filtered-fetch
    transport and enables Phase335's source-only selector only inside that
    filter-aware scope.  After the commit/tree graph is imported, checkout
    materializes exactly the unresolved blobs reachable from the selected
    worktree before the local branch is published.
    """

    filter_spec = _partial_unborn_candidate(repo, source)
    if filter_spec is None:
        return _ordinary_try_pull_unborn_upstream(repo, source)

    branch = _current_unborn_branch(repo)
    if branch is None:
        return None

    options = tuple(configured_server_options(repo, source.remote))
    with unborn_fetch_selection(allow_persistent_partial=True):
        with partial_filter_transport(
            repo,
            source.remote,
            filter_spec,
            server_options=options,
        ):
            fetched = fetch_porcelain(repo, source.remote)

    target_sha = _fetched_upstream_oid(fetched, source)
    if _current_unborn_branch(repo) != branch:
        raise UnbornPullBootstrapError(
            "local branch changed while preparing the initial partial pull"
        )

    # Match Phase337's transaction boundary: fetch/promisor/remote-tracking state
    # may already have advanced, but the local branch stays unborn until checkout
    # is known to be safe and all worktree-required promises are available.
    _preflight_local_transition(repo, target_sha)

    promises = tuple(sorted(_collect_checkout_promises(repo, target_sha)))
    materialized: Dict[str, str] = {}
    if promises:
        try:
            materialized = materialize_promised_objects(repo.pygit_dir, promises)
        except (RuntimeError, ValueError, KeyError) as exc:
            raise UnbornPullBootstrapError(
                "initial partial pull could not materialize checkout objects"
            ) from exc

    if _current_unborn_branch(repo) != branch:
        raise UnbornPullBootstrapError(
            "local branch changed while materializing the initial partial checkout"
        )

    repo._replace_worktree_from_commit(target_sha)
    repo.refs.set_branch(branch, target_sha, message="initial pull")

    return {
        "status": "initial-pull",
        "sha": target_sha,
        "conflicts": [],
        "fetch": fetched,
        "filter": filter_spec,
        "materialized": dict(materialized),
    }
