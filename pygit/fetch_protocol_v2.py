"""Command-scoped protocol-v2 fetch routing and negotiate-only support."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Dict, Iterator, Optional, Sequence
from urllib.parse import urlsplit

from .fetch_negotiation import (
    configured_negotiation_includes,
    plan_included_haves,
    reachable_commits,
    resolve_negotiation_tips,
)
from .protocol_v2 import SmartHttpV2QueryClient
from .protocol_v2_fetch import SmartHttpV2FetchClient
from .remote import NativeExporter, SmartHttpClient
from .remote_urls import fetch_url
from .repo import Repository


def protocol_v2_requested(repo: Optional[Repository]) -> bool:
    """Return whether a real repository explicitly selects protocol version 2."""
    if repo is None:
        return False
    getter = getattr(repo, "config_get", None)
    return callable(getter) and getter("protocol", "version") == "2"


@contextmanager
def protocol_v2_transport(
    *,
    server_options: Sequence[str] = (),
) -> Iterator[None]:
    """Make mature ``SmartHttpClient`` users prefer v2 for one command.

    ``server_options`` are forwarded, in order, to every v2 command request
    made by this fetch command. A server that ignores the v2 handshake retains
    the established v0 fallback only when no v2-only server option was asked
    for; sending a server option through legacy protocol would be misleading.
    """

    original_discover = SmartHttpClient.discover
    original_fetch = SmartHttpClient.fetch
    query_clients: Dict[str, SmartHttpV2QueryClient] = {}
    fetch_clients: Dict[str, SmartHttpV2FetchClient] = {}
    fallback: set[str] = set()
    options = tuple(server_options)

    def query_for(instance: SmartHttpClient) -> SmartHttpV2QueryClient:
        client = query_clients.get(instance.url)
        if client is None:
            client = SmartHttpV2QueryClient(
                instance.url,
                timeout=instance.timeout,
                server_options=options,
            )
            query_clients[instance.url] = client
        return client

    def fetch_for(instance: SmartHttpClient) -> SmartHttpV2FetchClient:
        client = fetch_clients.get(instance.url)
        if client is None:
            client = SmartHttpV2FetchClient(
                instance.url,
                timeout=instance.timeout,
                server_options=options,
            )
            fetch_clients[instance.url] = client
        return client

    def discover(self: SmartHttpClient):
        if self.url in fallback:
            return original_discover(self)
        advertisement = query_for(self).discover_refs()
        if advertisement is None:
            if options:
                raise RuntimeError("server options require protocol version 2")
            fallback.add(self.url)
            return original_discover(self)
        return advertisement

    def fetch(self: SmartHttpClient, haves=None, advertisement=None):
        if self.url in fallback:
            return original_fetch(self, haves=haves, advertisement=advertisement)
        result = fetch_for(self).fetch(haves=haves, advertisement=advertisement)
        if result is None:
            if options:
                raise RuntimeError("server options require protocol version 2")
            fallback.add(self.url)
            return original_fetch(self, haves=haves, advertisement=advertisement)
        return result

    SmartHttpClient.discover = discover
    SmartHttpClient.fetch = fetch
    try:
        yield
    finally:
        SmartHttpClient.discover = original_discover
        SmartHttpClient.fetch = original_fetch


def _source_url(repo: Repository, source: str) -> str:
    parsed = urlsplit(source)
    if parsed.scheme in {"http", "https"}:
        if not parsed.netloc:
            raise ValueError("invalid smart HTTP(S) fetch URL")
        return source
    return fetch_url(repo, source)


def _negotiation_have_map(repo: Repository, expressions: Sequence[str]) -> Dict[str, str]:
    """Map native SHA-1 negotiation haves back to local SHA-256 commits."""
    tips = resolve_negotiation_tips(repo, expressions)
    commits = reachable_commits(repo, tips)
    known: Dict[str, str] = {}
    for remote in repo.list_remotes():
        known.update(repo._read_native_map(remote))
    exporter = NativeExporter(repo.store, known_oids=known, have_shas=set(known))
    return {exporter.export_oid(oid): oid for oid in commits}


def _effective_negotiation_includes(
    repo: Repository,
    source: str,
    include: Sequence[str],
) -> Sequence[str]:
    if include:
        return include
    parsed = urlsplit(source)
    if parsed.scheme:
        return ()
    return configured_negotiation_includes(repo, source)


def negotiate_only(
    repo: Repository,
    *,
    source: str,
    restrict: Sequence[str],
    include: Sequence[str] = (),
    server_options: Sequence[str] = (),
) -> Sequence[str]:
    """Run a genuine protocol-v2 ACK-only negotiation."""
    if not restrict:
        raise RuntimeError(
            "--negotiate-only requires at least one --negotiation-restrict"
        )

    have_map = _negotiation_have_map(repo, restrict)
    haves = set(have_map)
    effective_include = _effective_negotiation_includes(repo, source, include)
    if effective_include:
        haves.update(plan_included_haves(repo, effective_include))

    client = SmartHttpV2FetchClient(
        _source_url(repo, source),
        server_options=server_options,
    )
    advertisement = client.discover_refs()
    if advertisement is None:
        raise RuntimeError("--negotiate-only requires protocol version 2")
    common = client.negotiate(haves=haves, advertisement=advertisement)
    if common is None:
        raise RuntimeError("--negotiate-only requires protocol version 2")

    return [have_map[oid] for oid in common if oid in have_map]
