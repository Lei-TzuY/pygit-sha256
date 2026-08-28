"""Per-remote protocol-v2 ``serverOption`` fallback for fetch.

Git uses ``remote.<name>.serverOption`` only when the command line does not
supply ``--server-option``.  This wrapper keeps named-remote identity alive
through multi-fetch orchestration, so remotes sharing a URL can still carry
independent option lists.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Dict, Iterator, Sequence, Tuple
from urllib.parse import urlsplit

from .config import GitConfig
from .fetch_cli import _default_fetch_remote
from . import fetch_cli_dry_run as fetch_frontend
from .fetch_negotiation import _ACTIVE_NEGOTIATION_REMOTE
from .fetch_protocol_v2 import protocol_v2_requested
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
    """Resolve the source using the established Phase203 parsing helpers."""
    forwarded = fetch_frontend._strip_option(argv, "--negotiate-only")
    forwarded = fetch_frontend._strip_option(forwarded, "--refetch")
    forwarded = fetch_frontend._strip_set_upstream(forwarded)
    forwarded, _restrict, _include = fetch_frontend._extract_negotiation_options(forwarded)
    positionals = fetch_frontend._fetch_positionals(forwarded)
    return positionals[0] if positionals else _default_fetch_remote(repo)


def _is_direct_source(source: str) -> bool:
    parsed = urlsplit(source)
    return parsed.scheme in {"http", "https"}


@contextmanager
def configured_server_option_transport(repo) -> Iterator[None]:
    """Prefer v2 per active named remote and attach its configured options.

    Unlike a URL-keyed policy, the cache key includes the active remote name.
    This matters for ``--multiple``/``--all`` when two remotes intentionally
    share an endpoint but configure different server options.
    """
    original_discover = SmartHttpClient.discover
    original_fetch = SmartHttpClient.fetch
    original_requested = fetch_frontend.protocol_v2_requested
    prefer_v2 = protocol_v2_requested(repo)

    QueryKey = Tuple[str, str, Tuple[str, ...]]
    query_clients: Dict[QueryKey, SmartHttpV2QueryClient] = {}
    fetch_clients: Dict[QueryKey, SmartHttpV2FetchClient] = {}
    fallback: set[QueryKey] = set()

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

    def discover(self: SmartHttpClient):
        key, options, use_v2 = active_policy(self)
        if not use_v2 or key in fallback:
            return original_discover(self)
        advertisement = query_for(self, key, options).discover_refs()
        if advertisement is None:
            if options:
                raise RuntimeError("server options require protocol version 2")
            fallback.add(key)
            return original_discover(self)
        return advertisement

    def fetch(self: SmartHttpClient, haves=None, advertisement=None):
        key, options, use_v2 = active_policy(self)
        if not use_v2 or key in fallback:
            return original_fetch(self, haves=haves, advertisement=advertisement)
        result = fetch_for(self, key, options).fetch(
            haves=haves,
            advertisement=advertisement,
        )
        if result is None:
            if options:
                raise RuntimeError("server options require protocol version 2")
            fallback.add(key)
            return original_fetch(self, haves=haves, advertisement=advertisement)
        return result

    SmartHttpClient.discover = discover
    SmartHttpClient.fetch = fetch
    # The outer scope now owns both protocol.version=2 and configured options;
    # suppress the inner Phase203 scope to avoid losing per-remote metadata.
    fetch_frontend.protocol_v2_requested = lambda _repo: False
    try:
        yield
    finally:
        fetch_frontend.protocol_v2_requested = original_requested
        SmartHttpClient.discover = original_discover
        SmartHttpClient.fetch = original_fetch


def run_fetch(argv: Sequence[str]) -> int:
    """Run Phase203 fetch with Git-compatible config fallback semantics."""
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
