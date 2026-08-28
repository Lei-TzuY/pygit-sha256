"""Fetch wrapper adding dry-run, shallow, negotiation and protocol-v2 policy."""

from __future__ import annotations

from contextlib import nullcontext
from typing import Optional, Sequence

from .fetch_cli import _default_fetch_remote, run_fetch as _run_fetch
from .fetch_dry_run import dry_run_repository
from .fetch_negotiation import (
    has_configured_negotiation_includes,
    negotiation_transport,
    resolve_negotiation_tips,
)
from .fetch_protocol_v2 import (
    negotiate_only,
    protocol_v2_requested,
    protocol_v2_transport,
)
from .fetch_refetch import refetch_transport
from .fetch_shallow import shallow_fetch_transport
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


def _extract_server_options(argv: Sequence[str]) -> tuple[list[str], list[str]]:
    """Strip ordered ``-o/--server-option`` values before legacy parsing."""
    forwarded: list[str] = []
    server_options: list[str] = []
    args = list(argv)
    options = True
    i = 0
    while i < len(args):
        arg = args[i]
        if options and arg == "--":
            options = False
            forwarded.append(arg)
            i += 1
            continue
        if options and arg in {"-o", "--server-option"}:
            if i + 1 >= len(args) or args[i + 1] == "--":
                raise ValueError(f"{arg} requires an option value")
            value = args[i + 1]
            if "\n" in value or "\x00" in value:
                raise ValueError("server option contains an invalid NUL or LF character")
            server_options.append(value)
            i += 2
            continue
        if options and arg.startswith("--server-option="):
            value = arg.split("=", 1)[1]
            if "\n" in value or "\x00" in value:
                raise ValueError("server option contains an invalid NUL or LF character")
            server_options.append(value)
            i += 1
            continue
        forwarded.append(arg)
        i += 1
    return forwarded, server_options


def _positive_depth(option: str, value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{option} requires a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"{option} requires a positive integer")
    return parsed


def _extract_shallow_options(
    argv: Sequence[str],
) -> tuple[list[str], Optional[int], Optional[int], bool]:
    """Strip Phase202 shallow controls before the established fetch parser."""
    forwarded: list[str] = []
    depth: Optional[int] = None
    deepen: Optional[int] = None
    unshallow = False
    args = list(argv)
    options = True
    i = 0

    while i < len(args):
        arg = args[i]
        if options and arg == "--":
            options = False
            forwarded.append(arg)
            i += 1
            continue
        if options and arg in {"--depth", "--deepen"}:
            if i + 1 >= len(args) or args[i + 1] == "--":
                raise ValueError(f"{arg} requires a positive integer")
            value = _positive_depth(arg, args[i + 1])
            if arg == "--depth":
                if depth is not None:
                    raise ValueError("--depth may be specified only once")
                depth = value
            else:
                if deepen is not None:
                    raise ValueError("--deepen may be specified only once")
                deepen = value
            i += 2
            continue
        if options and arg.startswith("--depth="):
            if depth is not None:
                raise ValueError("--depth may be specified only once")
            depth = _positive_depth("--depth", arg.split("=", 1)[1])
            i += 1
            continue
        if options and arg.startswith("--deepen="):
            if deepen is not None:
                raise ValueError("--deepen may be specified only once")
            deepen = _positive_depth("--deepen", arg.split("=", 1)[1])
            i += 1
            continue
        if options and arg == "--unshallow":
            if unshallow:
                raise ValueError("--unshallow may be specified only once")
            unshallow = True
            i += 1
            continue
        forwarded.append(arg)
        i += 1

    selected = int(depth is not None) + int(deepen is not None) + int(unshallow)
    if selected > 1:
        raise RuntimeError("--depth, --deepen, and --unshallow are mutually exclusive")
    return forwarded, depth, deepen, unshallow


def _extract_negotiation_options(
    argv: Sequence[str],
) -> tuple[list[str], list[str], list[str]]:
    """Strip negotiation-only CLI controls before the established parser."""
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


def _with_default_remote(argv: Sequence[str], remote: str) -> list[str]:
    forwarded = list(argv)
    if "--" in forwarded:
        forwarded.insert(forwarded.index("--"), remote)
    else:
        forwarded.append(remote)
    return forwarded


def _apply_set_upstream(argv: Sequence[str]) -> None:
    positionals = _fetch_positionals(argv)
    if not positionals:
        set_fetch_upstream(find_repo(), "origin", [])
        return
    remote = positionals[0]
    refspecs = positionals[1:]
    set_fetch_upstream(find_repo(), remote, refspecs)


def _optional_repo_for_config():
    try:
        return find_repo()
    except RuntimeError:
        return None


def _run_negotiate_only(
    forwarded: Sequence[str],
    restrict: Sequence[str],
    include: Sequence[str],
    server_options: Sequence[str],
) -> int:
    repo = find_repo()
    positionals = _fetch_positionals(forwarded)
    if len(positionals) > 1:
        raise RuntimeError("--negotiate-only does not accept fetch refspecs")
    source = positionals[0] if positionals else _default_fetch_remote(repo)
    if server_options:
        common = negotiate_only(
            repo,
            source=source,
            restrict=restrict,
            include=include,
            server_options=server_options,
        )
    else:
        common = negotiate_only(
            repo,
            source=source,
            restrict=restrict,
            include=include,
        )
    for sha in common:
        print(sha)
    return 0


def run_fetch(argv: Sequence[str]) -> int:
    """Run fetch with command-scoped porcelain and transport policies."""
    args = list(argv)
    wants_upstream = _option_requested(args, "--set-upstream")
    wants_refetch = _option_requested(args, "--refetch")
    wants_negotiate_only = _option_requested(args, "--negotiate-only")

    forwarded = _strip_set_upstream(args) if wants_upstream else args
    if wants_refetch:
        forwarded = _strip_option(forwarded, "--refetch")
    if wants_negotiate_only:
        forwarded = _strip_option(forwarded, "--negotiate-only")

    # Extract server-option before shallow controls so a value such as
    # ``--depth`` is metadata, not accidentally parsed as a fetch option.
    forwarded, server_options = _extract_server_options(forwarded)
    forwarded, depth, deepen, unshallow = _extract_shallow_options(forwarded)
    wants_shallow = depth is not None or deepen is not None or unshallow
    forwarded, restrict, include = _extract_negotiation_options(forwarded)

    if wants_negotiate_only:
        if wants_shallow:
            raise RuntimeError("--negotiate-only cannot be combined with shallow controls")
        if wants_refetch:
            raise RuntimeError("--negotiate-only cannot be combined with --refetch")
        if wants_upstream:
            raise RuntimeError("--negotiate-only cannot be combined with --set-upstream")
        for incompatible in ("--all", "--multiple", "--prefetch"):
            if _option_requested(forwarded, incompatible):
                raise RuntimeError(
                    f"--negotiate-only cannot be combined with {incompatible}"
                )
        return _run_negotiate_only(
            forwarded,
            restrict,
            include,
            server_options,
        )

    repo_for_protocol = _optional_repo_for_config()

    shallow_scope = nullcontext()
    if wants_shallow:
        if wants_refetch:
            raise RuntimeError("shallow fetch controls cannot be combined with --refetch")
        if restrict or include:
            raise RuntimeError(
                "shallow fetch controls cannot be combined with negotiation restrictions/includes"
            )
        for incompatible in ("--all", "--multiple", "--prefetch"):
            if _option_requested(forwarded, incompatible):
                raise RuntimeError(
                    f"shallow fetch controls cannot be combined with {incompatible}"
                )

        repo_for_protocol = find_repo()
        if not (protocol_v2_requested(repo_for_protocol) or server_options):
            raise RuntimeError(
                "shallow fetch controls currently require protocol.version=2"
            )
        positionals = _fetch_positionals(forwarded)
        remote = positionals[0] if positionals else _default_fetch_remote(repo_for_protocol)
        if remote not in repo_for_protocol.list_remotes():
            raise RuntimeError("shallow fetch controls currently require one named remote")
        if not positionals:
            forwarded = _with_default_remote(forwarded, remote)
        shallow_scope = shallow_fetch_transport(
            repo_for_protocol,
            remote,
            depth=depth,
            deepen=deepen,
            unshallow=unshallow,
        )

    if restrict or include:
        repo_for_negotiation = find_repo()
    elif wants_refetch:
        repo_for_negotiation = None
    else:
        repo_for_negotiation = repo_for_protocol

    configured_include = (
        repo_for_negotiation is not None
        and not wants_refetch
        and not wants_shallow
        and not include
        and has_configured_negotiation_includes(repo_for_negotiation)
    )

    if wants_refetch:
        if repo_for_negotiation is not None:
            if restrict:
                resolve_negotiation_tips(repo_for_negotiation, restrict)
            if include:
                resolve_negotiation_tips(repo_for_negotiation, include)
        transport_scope = refetch_transport()
    elif repo_for_negotiation is not None and (restrict or include or configured_include):
        if configured_include:
            transport_scope = negotiation_transport(
                repo_for_negotiation,
                restrict=restrict,
                include=include,
                use_config_include=True,
            )
        else:
            transport_scope = negotiation_transport(
                repo_for_negotiation,
                restrict=restrict,
                include=include,
            )
    else:
        transport_scope = nullcontext()

    if server_options:
        protocol_scope = protocol_v2_transport(server_options=server_options)
    elif protocol_v2_requested(repo_for_protocol):
        protocol_scope = protocol_v2_transport()
    else:
        protocol_scope = nullcontext()

    # Protocol scope is outermost so refetch/negotiation wrappers capture the
    # v2-aware method. Shallow import remains innermost and inside dry-run state.
    with protocol_scope:
        with transport_scope:
            with shallow_scope:
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
