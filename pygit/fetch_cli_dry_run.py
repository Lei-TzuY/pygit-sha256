"""Fetch wrapper adding dry-run, upstream, refetch, and negotiation policy."""

from __future__ import annotations

from contextlib import nullcontext
from typing import Sequence

from .fetch_cli import run_fetch as _run_fetch
from .fetch_dry_run import dry_run_repository
from .fetch_negotiation import (
    has_configured_negotiation_includes,
    negotiation_transport,
    resolve_negotiation_tips,
)
from .fetch_refetch import refetch_transport
from .fetch_upstream import set_fetch_upstream
from .tracking import find_repo


def _option_requested(argv: Sequence[str], option: str) -> bool:
    """Recognize an option only before ``--``; later tokens are refspecs."""
    for arg in argv:
        if arg == "--":
            return False
        if arg == option:
            return True
    return False


def _dry_run_requested(argv: Sequence[str]) -> bool:
    return _option_requested(argv, "--dry-run")


def _without_fetch_head_writes(argv: Sequence[str]) -> list[str]:
    forwarded: list[str] = []
    options = True
    for arg in argv:
        if options and arg == "--":
            options = False
            forwarded.append(arg)
            continue
        if options and arg in {"--dry-run", "--write-fetch-head", "--no-write-fetch-head"}:
            continue
        forwarded.append(arg)

    if "--" in forwarded:
        forwarded.insert(forwarded.index("--"), "--no-write-fetch-head")
    else:
        forwarded.append("--no-write-fetch-head")
    return forwarded


def _strip_option(argv: Sequence[str], option: str) -> list[str]:
    forwarded: list[str] = []
    options = True
    for arg in argv:
        if options and arg == "--":
            options = False
            forwarded.append(arg)
            continue
        if options and arg == option:
            continue
        forwarded.append(arg)
    return forwarded


def _strip_set_upstream(argv: Sequence[str]) -> list[str]:
    return _strip_option(argv, "--set-upstream")


def _extract_negotiation_options(
    argv: Sequence[str],
) -> tuple[list[str], list[str], list[str]]:
    """Strip negotiation-only CLI controls before the established parser.

    Returns ``(forwarded, restrict, include)``.  ``--negotiation-tip`` remains
    accepted as the historical spelling of current Git's preferred
    ``--negotiation-restrict`` name.
    """
    forwarded: list[str] = []
    restrict: list[str] = []
    include: list[str] = []
    args = list(argv)
    options = True
    i = 0
    names = {
        "--negotiation-restrict": restrict,
        "--negotiation-tip": restrict,
        "--negotiation-include": include,
    }

    while i < len(args):
        arg = args[i]
        if options and arg == "--":
            options = False
            forwarded.append(arg)
            i += 1
            continue

        handled = False
        if options:
            for name, target in names.items():
                if arg == name:
                    if i + 1 >= len(args) or args[i + 1] == "--":
                        raise ValueError(f"{name} requires a commit or ref pattern")
                    target.append(args[i + 1])
                    i += 2
                    handled = True
                    break
                prefix = name + "="
                if arg.startswith(prefix):
                    value = arg[len(prefix) :]
                    if not value:
                        raise ValueError(f"{name} requires a commit or ref pattern")
                    target.append(value)
                    i += 1
                    handled = True
                    break
        if handled:
            continue

        forwarded.append(arg)
        i += 1

    return forwarded, restrict, include


def _fetch_positionals(argv: Sequence[str]) -> list[str]:
    """Return repository/refspec positionals for the current fetch grammar."""
    result: list[str] = []
    args = list(argv)
    i = 0
    options = True
    while i < len(args):
        arg = args[i]
        if options and arg == "--":
            options = False
            i += 1
            continue
        if options and arg == "--refmap":
            i += 2
            continue
        if options and arg.startswith("--refmap="):
            i += 1
            continue
        if options and arg.startswith("-"):
            i += 1
            continue
        result.append(arg)
        i += 1
    return result


def _apply_set_upstream(argv: Sequence[str]) -> None:
    positionals = _fetch_positionals(argv)
    if not positionals:
        set_fetch_upstream(find_repo(), "origin", [])
        return
    remote = positionals[0]
    refspecs = positionals[1:]
    set_fetch_upstream(find_repo(), remote, refspecs)


def run_fetch(argv: Sequence[str]) -> int:
    """Run fetch with command-scoped porcelain and transport policies."""
    args = list(argv)
    wants_upstream = _option_requested(args, "--set-upstream")
    wants_refetch = _option_requested(args, "--refetch")
    forwarded = _strip_set_upstream(args) if wants_upstream else args
    if wants_refetch:
        forwarded = _strip_option(forwarded, "--refetch")

    forwarded, restrict, include = _extract_negotiation_options(forwarded)
    repo_for_negotiation = (
        find_repo()
        if restrict or include or not wants_refetch
        else None
    )

    # Git accepts negotiation controls alongside --refetch, but refetch's core
    # promise is a fresh transfer without local have negotiation.  Validate
    # explicit CLI tips while preserving that empty-have behavior. Per-remote
    # negotiationInclude config is therefore intentionally inactive under
    # refetch as well.
    if wants_refetch:
        if repo_for_negotiation is not None:
            if restrict:
                resolve_negotiation_tips(repo_for_negotiation, restrict)
            if include:
                resolve_negotiation_tips(repo_for_negotiation, include)
        transport_scope = refetch_transport()
    elif repo_for_negotiation is not None and (
        restrict
        or include
        or has_configured_negotiation_includes(repo_for_negotiation)
    ):
        if include:
            # Preserve the Phase197 transport call shape when command-line
            # include tips are explicit.  The Phase198-only keyword is needed
            # only when the per-remote configuration fallback is active.
            transport_scope = negotiation_transport(
                repo_for_negotiation,
                restrict=restrict,
                include=include,
            )
        else:
            transport_scope = negotiation_transport(
                repo_for_negotiation,
                restrict=restrict,
                include=include,
                use_config_include=True,
            )
    else:
        transport_scope = nullcontext()

    with transport_scope:
        if _dry_run_requested(forwarded):
            repo = find_repo()
            with dry_run_repository(repo):
                code = _run_fetch(_without_fetch_head_writes(forwarded))
                if code == 0 and wants_upstream:
                    _apply_set_upstream(forwarded)
                return code

        code = _run_fetch(forwarded)
        if code == 0 and wants_upstream:
            _apply_set_upstream(forwarded)
        return code
