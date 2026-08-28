"""Phase212 fetch-side partial clone foundation.

This layer deliberately implements filtered *fetch* before filtered clone.
A clone must populate a worktree and therefore needs on-demand promisor object
materialization first.  Filtered fetch can safely store refs/history while
leaving selected blobs promised for a later phase.
"""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from typing import Dict, Iterator, Optional, Sequence, Tuple

from . import fetch_configured, fetch_porcelain, fetch_server_option_config
from .fetch_cli import _default_fetch_remote
from .fetch_importer import PromisorFilteredNativeImporter
from .fetch_update_shallow_composition import run_fetch as _run_fetch
from .protocol_v2_fetch import SmartHttpV2FetchClient, V2FetchResult, build_fetch_request
from .remote import Advertisement, PackParser, SmartHttpClient
from .tracking import find_repo


def _validate_filter_spec(value: str) -> str:
    if "\n" in value or "\x00" in value:
        raise ValueError("--filter contains an invalid NUL or LF character")
    if value == "blob:none":
        return value
    prefix = "blob:limit="
    if value.startswith(prefix):
        raw = value[len(prefix) :]
        try:
            limit = int(raw, 10)
        except ValueError as exc:
            raise ValueError("--filter blob:limit requires a positive byte count") from exc
        if limit <= 0:
            raise ValueError("--filter blob:limit requires a positive byte count")
        return f"blob:limit={limit}"
    raise RuntimeError(
        "Phase212 supports only --filter=blob:none and --filter=blob:limit=<bytes>"
    )


def extract_filter_option(argv: Sequence[str]) -> tuple[list[str], Optional[str]]:
    """Strip one fetch filter before established fetch parsers see it."""
    result: list[str] = []
    selected: Optional[str] = None
    args = list(argv)
    options = True
    i = 0
    while i < len(args):
        arg = args[i]
        if options and arg == "--":
            options = False
            result.append(arg)
            i += 1
            continue
        if options and arg == "--filter":
            if selected is not None:
                raise ValueError("--filter may be specified only once")
            if i + 1 >= len(args) or args[i + 1] == "--":
                raise ValueError("--filter requires a filter specification")
            selected = _validate_filter_spec(args[i + 1])
            i += 2
            continue
        if options and arg.startswith("--filter="):
            if selected is not None:
                raise ValueError("--filter may be specified only once")
            selected = _validate_filter_spec(arg.split("=", 1)[1])
            i += 1
            continue
        result.append(arg)
        i += 1
    return result, selected


def _build_filtered_fetch_request(
    capabilities,
    wants: Sequence[str],
    *,
    haves=(),
    filter_spec: str,
    server_options: Sequence[str] = (),
) -> bytes:
    if not capabilities.feature("fetch", "filter"):
        raise RuntimeError("Remote protocol-v2 fetch does not advertise filter")
    body = build_fetch_request(
        capabilities,
        wants,
        haves=haves,
        done=True,
        server_options=server_options,
    )
    needle = b"want "
    position = body.find(needle)
    if position < 4:
        raise ValueError("protocol-v2 fetch request has no want line")
    position -= 4
    payload = f"filter {filter_spec}\n".encode()
    packet = f"{len(payload) + 4:04x}".encode() + payload
    return body[:position] + packet + body[position:]


def _filtered_v2_fetch(
    client: SmartHttpV2FetchClient,
    *,
    haves,
    advertisement: Optional[Advertisement],
    filter_spec: str,
):
    capabilities = client.discover_capabilities()
    if capabilities is None:
        raise RuntimeError("filtered fetch requires protocol version 2")
    if not capabilities.supports("fetch"):
        raise RuntimeError("Remote protocol-v2 server does not advertise fetch")
    advertisement = advertisement or client._discover_refs_with_capabilities(capabilities)
    wants = client._wants(advertisement)
    if not wants:
        raise RuntimeError("Remote repository does not advertise any refs.")
    body = _build_filtered_fetch_request(
        capabilities,
        wants,
        haves=haves or (),
        filter_spec=filter_spec,
        server_options=client.server_options,
    )
    parsed = client._post_fetch(body)
    if parsed.pack is None:
        raise ValueError("protocol-v2 filtered fetch response did not contain a packfile")
    return V2FetchResult(
        advertisement,
        PackParser(parsed.pack).parse(),
        parsed.shallow,
        parsed.unshallow,
    )


def _fetch_import_sources_filtered(
    repo,
    client,
    advertisement: Advertisement,
    source_oids: Dict[str, str],
    native_map: Dict[str, str],
    known_by_native: Dict[str, str],
):
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
    active = _ACTIVE_FILTER
    if active is None:
        raise RuntimeError("filtered importer used without an active filter")
    remote, filter_spec = active
    importer = PromisorFilteredNativeImporter(
        repo.store,
        result.objects,
        known=known_by_native,
        remote=remote,
        filter_spec=filter_spec,
    )
    imported = {
        refname: importer.import_oid(native_oid)
        for refname, native_oid in source_oids.items()
    }
    known_by_native.update(importer.converted)
    native_map.update(
        {local_oid: native_oid for native_oid, local_oid in importer.converted.items()}
    )
    return imported, len(result.objects)


_ACTIVE_FILTER: Optional[Tuple[str, str]] = None


@contextmanager
def partial_filter_transport(
    repo,
    remote: str,
    filter_spec: str,
    *,
    server_options: Sequence[str] = (),
) -> Iterator[None]:
    """Own v2 routing/import for one named-remote filtered fetch command."""
    global _ACTIVE_FILTER
    if remote not in repo.list_remotes():
        raise RuntimeError("filtered fetch currently requires one named remote")

    original_discover = SmartHttpClient.discover
    original_fetch = SmartHttpClient.fetch
    original_importers = (
        fetch_configured._fetch_import_sources,
        fetch_porcelain._fetch_import_sources,
    )
    frontend = fetch_server_option_config.fetch_frontend
    original_requested = frontend.protocol_v2_requested
    original_transport = frontend.protocol_v2_transport
    original_has_config = fetch_server_option_config.has_configured_server_options

    clients: Dict[Tuple[str, int], SmartHttpV2FetchClient] = {}

    def v2_for(instance: SmartHttpClient) -> SmartHttpV2FetchClient:
        key = (instance.url, instance.timeout)
        client = clients.get(key)
        if client is None:
            client = SmartHttpV2FetchClient(
                instance.url,
                timeout=instance.timeout,
                server_options=server_options,
            )
            clients[key] = client
        return client

    def discover(self: SmartHttpClient):
        advertisement = v2_for(self).discover_refs()
        if advertisement is None:
            raise RuntimeError("filtered fetch requires protocol version 2")
        return advertisement

    def fetch(self: SmartHttpClient, haves=None, advertisement=None):
        return _filtered_v2_fetch(
            v2_for(self),
            haves=haves,
            advertisement=advertisement,
            filter_spec=filter_spec,
        )

    previous_active = _ACTIVE_FILTER
    _ACTIVE_FILTER = (remote, filter_spec)
    SmartHttpClient.discover = discover
    SmartHttpClient.fetch = fetch
    fetch_configured._fetch_import_sources = _fetch_import_sources_filtered
    fetch_porcelain._fetch_import_sources = _fetch_import_sources_filtered

    # This outer scope owns protocol-v2 and configured server-option routing.
    # The existing inner policy/negotiation/dry-run stack remains intact.
    frontend.protocol_v2_requested = lambda _repo: True
    frontend.protocol_v2_transport = lambda *args, **kwargs: nullcontext()
    fetch_server_option_config.has_configured_server_options = lambda _repo: False
    try:
        yield
    finally:
        fetch_server_option_config.has_configured_server_options = original_has_config
        frontend.protocol_v2_transport = original_transport
        frontend.protocol_v2_requested = original_requested
        fetch_configured._fetch_import_sources, fetch_porcelain._fetch_import_sources = original_importers
        SmartHttpClient.fetch = original_fetch
        SmartHttpClient.discover = original_discover
        _ACTIVE_FILTER = previous_active


def _option_side(argv: Sequence[str]) -> list[str]:
    args = list(argv)
    return args[: args.index("--")] if "--" in args else args


def _reject_incompatible(argv: Sequence[str]) -> None:
    option_side = _option_side(argv)
    incompatible = (
        "--all",
        "--multiple",
        "--prefetch",
        "--refetch",
        "--negotiate-only",
        "--update-shallow",
        "--depth",
        "--deepen",
        "--unshallow",
        "--shallow-since",
        "--shallow-exclude",
    )
    for name in incompatible:
        if any(arg == name or arg.startswith(name + "=") for arg in option_side):
            raise RuntimeError(f"--filter cannot currently be combined with {name}")


def run_fetch(argv: Sequence[str]) -> int:
    forwarded, filter_spec = extract_filter_option(argv)
    if filter_spec is None:
        return _run_fetch(forwarded)

    _reject_incompatible(forwarded)
    repo = find_repo()

    # Extract explicit options before positional parsing. Fetch's historical -o
    # spelling takes a following payload which must not be mistaken for a remote.
    without_server, explicit_options = (
        fetch_server_option_config.fetch_frontend._extract_server_options(forwarded)
    )
    parse_args, _depth, _deepen, _unshallow = (
        fetch_server_option_config.fetch_frontend._extract_shallow_options(without_server)
    )
    parse_args, _restrict, _include = (
        fetch_server_option_config.fetch_frontend._extract_negotiation_options(parse_args)
    )
    positionals = fetch_server_option_config.fetch_frontend._fetch_positionals(parse_args)
    if len(positionals) > 1:
        raise RuntimeError("filtered fetch currently accepts one named remote and no refspecs")
    remote = positionals[0] if positionals else _default_fetch_remote(repo)
    if remote not in repo.list_remotes():
        raise RuntimeError("filtered fetch currently requires one named remote")

    configured = fetch_server_option_config.configured_server_options(repo, remote)
    effective_options = tuple(explicit_options or configured)

    # Server options are owned by the outer transport and removed from the
    # inner command to avoid a second protocol-v2 wrapper.
    inner_args = without_server
    if not positionals:
        if "--" in inner_args:
            inner_args = list(inner_args)
            inner_args.insert(inner_args.index("--"), remote)
        else:
            inner_args = [*inner_args, remote]

    with partial_filter_transport(
        repo,
        remote,
        filter_spec,
        server_options=effective_options,
    ):
        return _run_fetch(inner_args)
