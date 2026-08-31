"""Persisted partial-clone filter routing for ordinary fetch commands.

Native Git remembers ``remote.<name>.partialCloneFilter`` and automatically
reuses that filter on later fetches from the promisor remote.  The established
pygit filtered-fetch stack is explicit-command based, so an empty partial clone
could otherwise fall back to an ordinary unfiltered fetch once its remote grows a
first commit.  This module closes that gap without changing the mature filter
transport itself.
"""

from __future__ import annotations

from typing import Optional, Sequence

from .fetch_cli import _default_fetch_remote
from .fetch_partial import (
    _validate_filter_spec,
    extract_filter_option,
    run_fetch as _partial_run_fetch,
)
from .fetch_unborn_transition import (
    run_fetch as _unborn_run_fetch,
    unborn_fetch_selection,
)
from . import fetch_server_option_config
from .tracking import find_repo


def persisted_partial_filter(repo, remote: str) -> Optional[str]:
    """Return one validated persisted partial-clone filter for *remote*.

    A promisor remote without ``partialCloneFilter`` is not enough to infer an
    object filter: multi-promisor/cache configurations may deliberately omit one.
    Never invent a filter from the promisor bit or from ``extensions.partialClone``.
    """

    raw = repo.config_get("remote", f"{remote}.partialCloneFilter")
    if raw is None:
        return None
    return _validate_filter_spec(raw.strip())


def _target_remote(repo, argv: Sequence[str]) -> Optional[str]:
    """Resolve the named remote using the same parser seams as filtered fetch.

    Complex modes (``--all``, ``--multiple``, explicit refspecs, etc.) are not
    silently interpreted here.  Returning the first named/default remote is
    enough to discover persisted partial state; after filter injection the
    established filtered-fetch parser performs its own stricter compatibility
    checks and fails closed instead of entering an unfiltered transport.
    """

    args, _explicit_options = (
        fetch_server_option_config.fetch_frontend._extract_server_options(argv)
    )
    args, _depth, _deepen, _unshallow = (
        fetch_server_option_config.fetch_frontend._extract_shallow_options(args)
    )
    args, _restrict, _include = (
        fetch_server_option_config.fetch_frontend._extract_negotiation_options(args)
    )
    positionals = fetch_server_option_config.fetch_frontend._fetch_positionals(args)
    candidate = positionals[0] if positionals else _default_fetch_remote(repo)
    return candidate if candidate in repo.list_remotes() else None


def _inject_filter(argv: Sequence[str], filter_spec: str) -> list[str]:
    """Insert one explicit-equivalent filter before any ``--`` terminator."""

    return [f"--filter={filter_spec}", *list(argv)]


def run_fetch(argv: Sequence[str]) -> int:
    """Run fetch while honoring an explicit or persisted partial-clone filter.

    Explicit ``--filter`` remains authoritative.  When no command-line filter is
    present and the selected named remote records ``partialCloneFilter``, inject
    the validated persisted value into the already-tested Phase212 filtered-fetch
    stack.  The unborn source-only selector is allowed only inside those
    filter-aware executions.
    """

    _forwarded, explicit_filter = extract_filter_option(argv)
    if explicit_filter is not None:
        with unborn_fetch_selection(allow_persistent_partial=True):
            return _partial_run_fetch(argv)

    repo = find_repo()
    remote = _target_remote(repo, argv)
    if remote is None:
        return _unborn_run_fetch(argv)

    filter_spec = persisted_partial_filter(repo, remote)
    if filter_spec is None:
        return _unborn_run_fetch(argv)

    with unborn_fetch_selection(allow_persistent_partial=True):
        return _partial_run_fetch(_inject_filter(argv, filter_spec))
