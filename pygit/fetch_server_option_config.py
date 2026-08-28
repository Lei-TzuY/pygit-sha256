"""Per-remote protocol-v2 ``serverOption`` fallback for fetch.

Git uses ``remote.<name>.serverOption`` only when the command line does not
supply ``--server-option``. This reconciled wrapper keeps named-remote identity
through multi-fetch orchestration and composes those options with Phase202
shallow/deepen transport.
"""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from typing import Dict, Iterator, Sequence, Tuple
from urllib.parse import urlsplit

from .config import GitConfig
from .fetch_cli import _default_fetch_remote
from . import fetch_cli_dry_run as fetch_frontend
from .fetch_negotiation import _ACTIVE_NEGOTIATION_REMOTE
from .fetch_protocol_v2 import protocol_v2_requested
from .fetch_shallow import current_shallow_request
from .protocol_v2 import SmartHttpV2QueryClient
from .protocol_v2_fetch import SmartHttpV2FetchClient
from .remote import SmartHttpClient
from .tracking import find_repo


def configured_server_options(repo, remote: str) -> list[str]:
    """Return ordered effective ``remote.<name>.serverOption`` values."""
    return GitConfig(repo.pygit_dir).get_all("remote", f"{remote}.serverOption")


def has_configured_server_options(repo) -> bool:
    """Return whether any configured named remote has an effective option."""
    return any(configured_server_options(repo, remote) for remote in repo.list_remotes())


def _has_explicit_server_option(argv: Sequence[str]) -> bool:
    """Detect CLI server options before the standard ``--`` terminator."""
    args = list(argv)
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            return False
        if arg in {"-o", "--server-option"} or arg.startswith("--server-option="):
            return True
        i += 1
    return False


def _insert_server_options(argv: Sequence[str], options: Sequence[str]) -> list[str]:
    """Insert explicit-equivalent options before ``--`` for negotiate-only."""
    injected = [f"--server-option={value}" for value in options]
    result = list(argv)
    if "--" in result:
        index = result.index("--")
        return result[:index] + injected + result[index:]
    return injected + result


def _negotiate_only_source(argv: Sequence[str], repo) -> str:
    """Resolve the source using the established fetch parsing helpers."""
    forwarded = fetch_frontend._strip_option(argv, "--negotiate-only")
    forwarded = fetch_frontend._strip_option(forwarded, "--refetch")
    forwarded = fetch_frontend._strip_set_upstream(forwarded)
    forwarded, _server_options = fetch_frontend._extract_server_options(forwarded)
    forwarded, _depth, _deepen, _unshallow = fetch_frontend._extract_shallow_options(
        forwarded
    )
    forwarded, _restrict, _include = fetch_frontend._extract_negotiation_options(
        forwarded
    )
    positionals = fetch_frontend._fetch_positionals(forwarded)
    return positionals[0] if positionals else _default_fetch_remote(repo)


def _is_direct_source(source: str) -> bool:
    parsed = urlsplit(source)
    return parsed.scheme in {"http", "https"}


@contextmanager
def configured_server_option_transport(repo) -> Iterator[None]:
    """Prefer v2 per active named remote and attach its configured options.

    The active named remote is part of the cache key, so two remotes sharing one
    URL can retain independent ordered options and independent v0 fallback state.
    This outer transport also owns shallow/deepen forwarding; the inner generic
    protocol-v2 scope is suppressed to avoid nested method replacement.
    """
    original_discover = SmartHttpClient.discover
    original_fetch = SmartHttpClient.fetch
    original_requested = fetch_frontend.protocol_v2_requested
    original_transport = fetch_frontend.protocol_v2_transport
    prefer_v2 = protocol_v2_requested(repo)

    QueryKey = Tuple[str, str, Tuple[str, ...]]
    query_clients: Dict[QueryKey, SmartHttpV2QueryClient] = {}
    fetch_clients: Dict[QueryKey, SmartHttpV2FetchClient] = {}
    fallback: set[QueryKey] = set()
    shallow_sent: set[QueryKey] = set()

    def active_policy(instance: SmartHttpClient):
        remote = _ACTIVE_NEGOTIATION_REMOTE.get()
        options = tuple(configured_server_options(repo, remote)) if remote else ()
        use_v2 = prefer_v2 or bool(options)
        key: QueryKey = (remote or "", instance.url, options)
        return key, options, use_v2

    def query_for(instance: SmartHttpClient, key: QueryKey, options: Tuple[str, ...]):
        client = query_clients.get(key)
        if client is None:
            client = SmartHttpV2QueryClient(
                instance.url,
                timeout=instance.timeout,
                server_options=options,
            )
            query_clients[key] = client
        return client

    def fetch_for(instance: SmartHttpClient, key: QueryKey, options: Tuple[str, ...]):
        client = fetch_clients.get(key)
        if client is None:
            client = SmartHttpV2FetchClient(
                instance.url,
                timeout=instance.timeout,
                server_options=options,
            )
            fetch_clients[key] = client
        return client

    def strict_reason(options: Tuple[str, ...]):
        if current_shallow_request() is not None:
            return "shallow fetch requires protocol version 2"
        if options:
            return "server options require protocol version 2"
        return None

    def discover(self: SmartHttpClient):
        key, options, use_v2 = active_policy(self)
        reason = strict_reason(options)
        if not use_v2 or key in fallback:
            if reason:
                raise RuntimeError(reason)
            return original_discover(self)
        advertisement = query_for(self, key, options).discover_refs()
        if advertisement is None:
            if reason:
                raise RuntimeError(reason)
            fallback.add(key)
            return original_discover(self)
        return advertisement

    def fetch(self: SmartHttpClient, haves=None, advertisement=None):
        key, options, use_v2 = active_policy(self)
        request = current_shallow_request()
        reason = strict_reason(options)
        if not use_v2 or key in fallback:
            if reason:
                raise RuntimeError(reason)
            return original_fetch(self, haves=haves, advertisement=advertisement)

        if request is not None and key not in shallow_sent:
            result = fetch_for(self, key, options).fetch(
                haves=haves,
                advertisement=advertisement,
                shallow=request.shallow,
                deepen=request.deepen,
                deepen_relative=request.deepen_relative,
            )
            shallow_sent.add(key)
        else:
            result = fetch_for(self, key, options).fetch(
                haves=haves,
                advertisement=advertisement,
            )
        if result is None:
            if reason:
                raise RuntimeError(reason)
            fallback.add(key)
            return original_fetch(self, haves=haves, advertisement=advertisement)
        return result

    SmartHttpClient.discover = discover
    SmartHttpClient.fetch = fetch

    # The outer scope owns protocol routing. Report v2 as available to the
    # frontend's shallow guard while replacing its inner protocol context with a
    # no-op, so shallow semantics are preserved without double monkeypatching.
    fetch_frontend.protocol_v2_requested = lambda _repo: True
    fetch_frontend.protocol_v2_transport = lambda *args, **kwargs: nullcontext()
    try:
        yield
    finally:
        fetch_frontend.protocol_v2_transport = original_transport
        fetch_frontend.protocol_v2_requested = original_requested
        SmartHttpClient.discover = original_discover
        SmartHttpClient.fetch = original_fetch


def run_fetch(argv: Sequence[str]) -> int:
    """Run fetch with Git-compatible configured server-option precedence."""
    args = list(argv)
    if _has_explicit_server_option(args):
        # Command-line values override remote.<name>.serverOption entirely.
        return fetch_frontend.run_fetch(args)

    repo = find_repo()

    if fetch_frontend._option_requested(args, "--negotiate-only"):
        source = _negotiate_only_source(args, repo)
        if not _is_direct_source(source):
            options = configured_server_options(repo, source)
            if options:
                return fetch_frontend.run_fetch(_insert_server_options(args, options))
        return fetch_frontend.run_fetch(args)

    if not has_configured_server_options(repo):
        return fetch_frontend.run_fetch(args)

    with configured_server_option_transport(repo):
        return fetch_frontend.run_fetch(args)
