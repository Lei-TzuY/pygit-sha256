"""Phase211 composition for ``fetch --update-shallow`` and explicit shallow controls.

Phase210 deliberately kept ``--update-shallow`` separate from depth/deepen and
selector operations while the shallow-source safety path stabilized. Git accepts
those combinations: an explicit shallow-history option already opts into changing
the local shallow boundary, so the extra ``--update-shallow`` flag is redundant
rather than conflicting.

This wrapper preserves Phase210's warning-only safety for ordinary fetches and its
standalone ``--update-shallow`` transport. When ``--update-shallow`` is combined
with an explicit shallow mutation, it strips only that flag and delegates to the
established Phase202/208 shallow transport, which owns protocol-v2 negotiation,
stable foreign-parent importing, and SHA-256 ``.pygit/shallow`` persistence.
"""

from __future__ import annotations

from typing import Sequence

from . import fetch_server_option_config
from .fetch_shallow_selectors import run_fetch as _run_shallow_fetch
from .fetch_update_shallow import _extract_update_shallow, run_fetch as _run_fetch


_SHALLOW_MUTATION_OPTIONS = (
    "--depth",
    "--deepen",
    "--unshallow",
    "--shallow-since",
    "--shallow-exclude",
)


def _has_explicit_shallow_mutation(argv: Sequence[str]) -> bool:
    """Return whether the option side explicitly requests a shallow mutation.

    Server-option values are removed first because a perfectly valid payload such
    as ``-o --deepen=2`` belongs to the server and must never be reinterpreted as
    a client-side shallow option. The standard ``--`` terminator remains owned by
    the established extractors.
    """
    args, _server_options = (
        fetch_server_option_config.fetch_frontend._extract_server_options(list(argv))
    )
    option_side = args[: args.index("--")] if "--" in args else args
    return any(
        arg == option or arg.startswith(option + "=")
        for arg in option_side
        for option in _SHALLOW_MUTATION_OPTIONS
    )


def run_fetch(argv: Sequence[str]) -> int:
    """Compose ``--update-shallow`` with explicit shallow-history controls.

    The explicit shallow transport already updates ``.pygit/shallow`` from the
    server's ``shallow-info`` response, so entering Phase210's second importer
    scope would be both redundant and unsafe. Standalone ``--update-shallow`` and
    ordinary fetches continue through Phase210 unchanged.
    """
    forwarded, update_shallow = _extract_update_shallow(argv)
    if update_shallow and _has_explicit_shallow_mutation(forwarded):
        return _run_shallow_fetch(forwarded)
    return _run_fetch(argv)
