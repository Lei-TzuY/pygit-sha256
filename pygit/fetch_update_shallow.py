"""Git-style ``fetch --update-shallow`` safety and acceptance.

Ordinary protocol-v2 fetches refuse a server-provided ``shallow-info`` update
unless the user explicitly opts in.  With ``--update-shallow`` active, pygit
advertises the repository's current shallow boundary, imports a genuinely
truncated pack with the stable foreign-parent importer, and applies returned
boundary changes in repository-visible SHA-256 identity.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Dict, Iterator, Sequence

from . import fetch_configured, fetch_porcelain
from .fetch_cli import _default_fetch_remote
from .fetch_importer import StableShallowNativeImporter
from .fetch_server_option_config import (
    _has_explicit_server_option,
    has_configured_server_options,
)
from .fetch_shallow import (
    ShallowFetchRequest,
    _ACTIVE_SHALLOW_REQUEST,
    _apply_shallow_response,
    _native_boundaries,
    read_shallow,
)
from .fetch_shallow_selectors import run_fetch as _run_fetch
from .protocol_v2_fetch import SmartHttpV2FetchClient
from .remote import Advertisement
from .tracking import find_repo


def _extract_update_shallow(argv: Sequence[str]) -> tuple[list[str], bool]:
    forwarded: list[str] = []
    enabled = False
    options = True
    for arg in argv:
        if options and arg == "--":
            options = False
            forwarded.append(arg)
            continue
        if options and arg == "--update-shallow":
            enabled = True
            continue
        forwarded.append(arg)
    return forwarded, enabled


def _fetch_positionals(argv: Sequence[str]) -> list[str]:
    """Use the established modern fetch parser after stripping v2-only values."""
    from . import fetch_server_option_config

    args, _server_options = fetch_server_option_config.fetch_frontend._extract_server_options(
        list(argv)
    )
    args, _depth, _deepen, _unshallow = (
        fetch_server_option_config.fetch_frontend._extract_shallow_options(args)
    )
    args, _restrict, _include = (
        fetch_server_option_config.fetch_frontend._extract_negotiation_options(args)
    )
    return fetch_server_option_config.fetch_frontend._fetch_positionals(args)


def _with_default_remote(argv: Sequence[str], remote: str) -> list[str]:
    forwarded = list(argv)
    if "--" in forwarded:
        forwarded.insert(forwarded.index("--"), remote)
    else:
        forwarded.append(remote)
    return forwarded


def _fetch_import_sources_update_shallow(
    repo,
    client,
    advertisement: Advertisement,
    source_oids: Dict[str, str],
    native_map: Dict[str, str],
    known_by_native: Dict[str, str],
):
    """Import a shallow-source response without rewriting local SHA-256 commits."""
    if not source_oids:
        return {}, 0
    if all(oid in known_by_native for oid in source_oids.values()):
        return {name: known_by_native[oid] for name, oid in source_oids.items()}, 0

    selected = Advertisement(
        refs=dict(source_oids),
        capabilities=set(advertisement.capabilities),
        symrefs=dict(advertisement.symrefs),
    )
    result = client.fetch(haves=native_map.values(), advertisement=selected)
    if result is None:
        raise RuntimeError("--update-shallow requires protocol version 2")

    importer = StableShallowNativeImporter(
        repo.store,
        result.objects,
        known=known_by_native,
    )
    imported = {
        ref_name: importer.import_oid(native_oid)
        for ref_name, native_oid in source_oids.items()
    }
    known_by_native.update(importer.converted)
    native_map.update(
        {
            pygit_sha: native_oid
            for native_oid, pygit_sha in importer.converted.items()
        }
    )
    _apply_shallow_response(repo, result, known_by_native)
    return imported, len(result.objects)


@contextmanager
def reject_unrequested_shallow_updates() -> Iterator[None]:
    """Refuse protocol-v2 shallow boundary changes without explicit opt-in."""
    original = SmartHttpV2FetchClient.fetch

    def guarded(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        if result is not None and (
            tuple(getattr(result, "shallow", ()) or ())
            or tuple(getattr(result, "unshallow", ()) or ())
        ):
            raise RuntimeError(
                "fetch from shallow remote would update .pygit/shallow; "
                "use --update-shallow to accept it"
            )
        return result

    SmartHttpV2FetchClient.fetch = guarded
    try:
        yield
    finally:
        SmartHttpV2FetchClient.fetch = original


@contextmanager
def update_shallow_transport(repo, remote: str) -> Iterator[None]:
    """Accept server shallow-info and persist it in local SHA-256 identity."""
    if remote not in repo.list_remotes():
        raise RuntimeError("--update-shallow currently requires one named remote")

    local = read_shallow(repo)
    native = _native_boundaries(repo, remote, local) if local else ()
    request = ShallowFetchRequest(
        shallow=native,
        deepen=None,  # type: ignore[arg-type] -- no depth change is requested
        deepen_relative=False,
        unshallow=False,
    )

    originals = [
        fetch_configured._fetch_import_sources,
        fetch_porcelain._fetch_import_sources,
    ]
    token = _ACTIVE_SHALLOW_REQUEST.set(request)
    try:
        fetch_configured._fetch_import_sources = _fetch_import_sources_update_shallow
        fetch_porcelain._fetch_import_sources = _fetch_import_sources_update_shallow
        yield
    finally:
        (
            fetch_configured._fetch_import_sources,
            fetch_porcelain._fetch_import_sources,
        ) = originals
        _ACTIVE_SHALLOW_REQUEST.reset(token)


def run_fetch(argv: Sequence[str]) -> int:
    forwarded, update_shallow = _extract_update_shallow(argv)

    if not update_shallow:
        with reject_unrequested_shallow_updates():
            return _run_fetch(forwarded)

    option_side = forwarded[: forwarded.index("--")] if "--" in forwarded else forwarded
    for incompatible in (
        "--depth",
        "--deepen",
        "--unshallow",
        "--shallow-since",
        "--shallow-exclude",
        "--all",
        "--multiple",
        "--prefetch",
        "--refetch",
        "--negotiate-only",
    ):
        if any(
            arg == incompatible or arg.startswith(incompatible + "=")
            for arg in option_side
        ):
            raise RuntimeError(f"--update-shallow cannot be combined with {incompatible}")

    repo = find_repo()
    if (
        repo.config_get("protocol", "version") != "2"
        and not _has_explicit_server_option(forwarded)
        and not has_configured_server_options(repo)
    ):
        raise RuntimeError("--update-shallow currently requires protocol.version=2")

    positionals = _fetch_positionals(forwarded)
    remote = positionals[0] if positionals else _default_fetch_remote(repo)
    if not positionals:
        forwarded = _with_default_remote(forwarded, remote)

    with update_shallow_transport(repo, remote):
        return _run_fetch(forwarded)
