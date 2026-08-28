"""Phase208 Git-style --shallow-since/--shallow-exclude fetch controls.

This layer composes with the established Phase202/204/207 shallow importer and
protocol-v2 routing without widening older public call shapes. Repository-visible
shallow boundaries remain local SHA-256 IDs; date/ref selectors are converted
only into protocol-v2 request metadata at the smart-HTTP boundary.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator, Optional, Sequence, Tuple

from . import fetch_configured, fetch_porcelain
from . import fetch_protocol_v2, fetch_server_option_config
from .fetch_cli import _default_fetch_remote
from .fetch_server_option_config import run_fetch as _run_fetch
from .fetch_shallow import (
    _fetch_import_sources_shallow,
    _native_boundaries,
    read_shallow,
)
from .protocol_v2_fetch import SmartHttpV2FetchClient, V2FetchResult, build_fetch_request
from .remote import PackParser
from .tracking import find_repo


@dataclass(frozen=True)
class ShallowSelectorRequest:
    shallow: Tuple[str, ...]
    deepen: Optional[int]
    deepen_relative: bool
    unshallow: bool
    deepen_since: Optional[int]
    deepen_not: Tuple[str, ...]


_ACTIVE_SELECTOR_REQUEST: ContextVar[Optional[ShallowSelectorRequest]] = ContextVar(
    "pygit_shallow_selector_request", default=None
)


def current_selector_request() -> Optional[ShallowSelectorRequest]:
    return _ACTIVE_SELECTOR_REQUEST.get()


def _pkt_line(payload: bytes) -> bytes:
    return f"{len(payload) + 4:04x}".encode() + payload


def _parse_shallow_since(value: str) -> int:
    """Parse a conservative Git-compatible date subset into epoch seconds.

    Numeric Unix timestamps and ISO-8601 dates/times are accepted. Naive values
    are interpreted in the local timezone, matching normal command-line date
    expectations more closely than silently forcing UTC.
    """
    text = value.strip()
    if not text:
        raise ValueError("--shallow-since requires a date")
    try:
        timestamp = int(text, 10)
    except ValueError:
        normalized = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise ValueError(
                "--shallow-since requires a Unix timestamp or ISO-8601 date"
            ) from exc
        if parsed.tzinfo is None:
            parsed = parsed.astimezone()
        timestamp = int(parsed.timestamp())
    if timestamp < 0:
        raise ValueError("--shallow-since requires a non-negative timestamp")
    return timestamp


def _validate_exclude(value: str) -> str:
    if not value or "\n" in value or "\x00" in value:
        raise ValueError("--shallow-exclude requires a valid remote ref")
    return value


def extract_shallow_selectors(
    argv: Sequence[str],
) -> tuple[list[str], Optional[int], list[str]]:
    """Strip selector options before the legacy fetch grammar sees them."""
    forwarded: list[str] = []
    since: Optional[int] = None
    excludes: list[str] = []
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
        if options and arg == "--shallow-since":
            if i + 1 >= len(args) or args[i + 1] == "--":
                raise ValueError("--shallow-since requires a date")
            if since is not None:
                raise ValueError("--shallow-since may be specified only once")
            since = _parse_shallow_since(args[i + 1])
            i += 2
            continue
        if options and arg.startswith("--shallow-since="):
            if since is not None:
                raise ValueError("--shallow-since may be specified only once")
            since = _parse_shallow_since(arg.split("=", 1)[1])
            i += 1
            continue
        if options and arg == "--shallow-exclude":
            if i + 1 >= len(args) or args[i + 1] == "--":
                raise ValueError("--shallow-exclude requires a remote ref")
            excludes.append(_validate_exclude(args[i + 1]))
            i += 2
            continue
        if options and arg.startswith("--shallow-exclude="):
            excludes.append(_validate_exclude(arg.split("=", 1)[1]))
            i += 1
            continue
        forwarded.append(arg)
        i += 1
    return forwarded, since, excludes


def _selector_fetch_request(
    capabilities,
    wants,
    *,
    haves=(),
    shallow=(),
    deepen_since: Optional[int],
    deepen_not: Sequence[str],
) -> bytes:
    """Extend the proven Phase202 request with protocol-v2 selector lines."""
    if not capabilities.feature("fetch", "shallow"):
        raise RuntimeError("Remote protocol-v2 fetch does not advertise shallow")
    body = build_fetch_request(
        capabilities,
        wants,
        haves=haves,
        done=True,
        shallow=shallow,
    )
    insertion = body.find(b"want ")
    if insertion < 4:
        raise ValueError("protocol-v2 fetch request has no want line")
    insertion -= 4
    extra = b""
    if deepen_since is not None:
        extra += _pkt_line(f"deepen-since {deepen_since}\n".encode())
    for revision in deepen_not:
        extra += _pkt_line(f"deepen-not {revision}\n".encode())
    return body[:insertion] + extra + body[insertion:]


@contextmanager
def shallow_selector_transport(repo, remote: str, *, since: Optional[int], excludes: Sequence[str]) -> Iterator[None]:
    if remote not in repo.list_remotes():
        raise RuntimeError("shallow selectors currently require one named remote")
    local_boundaries = read_shallow(repo)
    if not local_boundaries:
        raise RuntimeError("--shallow-since/--shallow-exclude require an existing shallow repository")
    native = _native_boundaries(repo, remote, local_boundaries)
    request = ShallowSelectorRequest(
        shallow=native,
        deepen=None,
        deepen_relative=False,
        unshallow=False,
        deepen_since=since,
        deepen_not=tuple(excludes),
    )

    original_importers = [fetch_configured._fetch_import_sources, fetch_porcelain._fetch_import_sources]
    original_protocol_current = fetch_protocol_v2.current_shallow_request
    original_config_current = fetch_server_option_config.current_shallow_request
    original_v2_fetch = SmartHttpV2FetchClient.fetch

    def selector_current():
        return current_selector_request()

    def fetch_with_selectors(self, haves=None, advertisement=None, **kwargs):
        active = current_selector_request()
        if active is None:
            return original_v2_fetch(self, haves=haves, advertisement=advertisement, **kwargs)
        capabilities = self.discover_capabilities()
        if capabilities is None:
            return None
        if not capabilities.supports("fetch"):
            raise RuntimeError("Remote protocol-v2 server does not advertise fetch")
        advertisement = advertisement or self._discover_refs_with_capabilities(capabilities)
        wants = self._wants(advertisement)
        if not wants:
            raise RuntimeError("Remote repository does not advertise any refs.")
        body = _selector_fetch_request(
            capabilities,
            wants,
            haves=haves or (),
            shallow=active.shallow,
            deepen_since=active.deepen_since,
            deepen_not=active.deepen_not,
        )
        parsed = self._post_fetch(body)
        if parsed.pack is None:
            raise ValueError("protocol-v2 fetch response did not contain a packfile")
        return V2FetchResult(
            advertisement,
            PackParser(parsed.pack).parse(),
            parsed.shallow,
            parsed.unshallow,
        )

    token = _ACTIVE_SELECTOR_REQUEST.set(request)
    try:
        fetch_configured._fetch_import_sources = _fetch_import_sources_shallow
        fetch_porcelain._fetch_import_sources = _fetch_import_sources_shallow
        fetch_protocol_v2.current_shallow_request = selector_current
        fetch_server_option_config.current_shallow_request = selector_current
        SmartHttpV2FetchClient.fetch = fetch_with_selectors
        yield
    finally:
        SmartHttpV2FetchClient.fetch = original_v2_fetch
        fetch_server_option_config.current_shallow_request = original_config_current
        fetch_protocol_v2.current_shallow_request = original_protocol_current
        fetch_configured._fetch_import_sources, fetch_porcelain._fetch_import_sources = original_importers
        _ACTIVE_SELECTOR_REQUEST.reset(token)


def run_fetch(argv: Sequence[str]) -> int:
    forwarded, since, excludes = extract_shallow_selectors(argv)
    if since is None and not excludes:
        return _run_fetch(forwarded)

    # Protocol-v2 says deepen-since/deepen-not cannot be combined with deepen.
    option_side = forwarded[: forwarded.index("--")] if "--" in forwarded else forwarded
    if any(
        arg == "--depth" or arg.startswith("--depth=") or arg == "--deepen" or arg.startswith("--deepen=") or arg == "--unshallow"
        for arg in option_side
    ):
        raise RuntimeError("--shallow-since/--shallow-exclude cannot be combined with depth/deepen/unshallow")
    for incompatible in ("--all", "--multiple", "--prefetch", "--refetch", "--negotiate-only"):
        if incompatible in option_side:
            raise RuntimeError(f"shallow selectors cannot be combined with {incompatible}")

    repo = find_repo()
    if repo.config_get("protocol", "version") != "2" and not fetch_server_option_config._has_explicit_server_option(forwarded):
        if not fetch_server_option_config.has_configured_server_options(repo):
            raise RuntimeError("shallow selectors currently require protocol.version=2")

    # Reuse the established positional parser after selector removal.
    args_for_positionals = list(forwarded)
    args_for_positionals, _server_options = fetch_server_option_config.fetch_frontend._extract_server_options(args_for_positionals)
    args_for_positionals, _depth, _deepen, _unshallow = fetch_server_option_config.fetch_frontend._extract_shallow_options(args_for_positionals)
    args_for_positionals, _restrict, _include = fetch_server_option_config.fetch_frontend._extract_negotiation_options(args_for_positionals)
    positionals = fetch_server_option_config.fetch_frontend._fetch_positionals(args_for_positionals)
    remote = positionals[0] if positionals else _default_fetch_remote(repo)
    if not positionals:
        if "--" in forwarded:
            forwarded.insert(forwarded.index("--"), remote)
        else:
            forwarded.append(remote)

    with shallow_selector_transport(repo, remote, since=since, excludes=excludes):
        return _run_fetch(forwarded)
