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
    """Return whether a real repository explicitly selects protocol version 2.

    Older fetch-wrapper regressions intentionally use lightweight stand-ins for
    a repository. Because protocol selection is optional, those stand-ins must
    remain transparent rather than requiring a ``config_get`` method.
    """
    if repo is None:
        return False
    getter = getattr(repo, "config_get", None)
    return callable(getter) and getter("protocol", "version") == "2"


@contextmanager
def protocol_v2_transport() -> Iterator[None]:
    """Make mature ``SmartHttpClient`` users prefer v2 for one command.

    The fetch stack already centralizes refspecs, tag policy, pruning, atomic
    updates, dry-run, refetch and negotiation controls around
    ``SmartHttpClient``. Replacing only its transport methods lets all of that
    porcelain reuse the Phase200 v2 transport without a parallel implementation.
    Servers that ignore the v2 handshake keep the established v0 fallback.
    """

    original_discover = SmartHttpClient.discover
    original_fetch = SmartHttpClient.fetch
    query_clients: Dict[str, SmartHttpV2QueryClient] = {}
    fetch_clients: Dict[str, SmartHttpV2FetchClient] = {}
    fallback: set[str] = set()

    def query_for(instance: SmartHttpClient) -> SmartHttpV2QueryClient:
        client = query_clients.get(instance.url)
        if client is None:
            client = SmartHttpV2QueryClient(instance.url, timeout=instance.timeout)
            query_clients[instance.url] = client
        return client

    def fetch_for(instance: SmartHttpClient) -> SmartHttpV2FetchClient:
        client = fetch_clients.get(instance.url)
        if client is None:
            client = SmartHttpV2FetchClient(instance.url, timeout=instance.timeout)
            fetch_clients[instance.url] = client
        return client

    def discover(self: SmartHttpClient):
        if self.url in fallback:
            return original_discover(self)
        advertisement = query_for(self).discover_refs()
        if advertisement is None:
            fallback.add(self.url)
            return original_discover(self)
        return advertisement

    def fetch(self: SmartHttpClient, haves=None, advertisement=None):
        if self.url in fallback:
            return original_fetch(self, haves=haves, advertisement=advertisement)
        result = fetch_for(self).fetch(haves=haves, advertisement=advertisement)
        if result is None:
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
    """Apply Git's CLI-over-remote-config negotiationInclude precedence."""
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
) -> Sequence[str]:
    """Run a genuine protocol-v2 ACK-only negotiation.

    A v0 answer is an error rather than a fallback because Git's negotiate-only
    operation depends on v2 ``wait-for-done``. Output is translated back to
    pygit's repository-visible SHA-256 commit identity.
    """
    if not restrict:
        raise RuntimeError(
            "--negotiate-only requires at least one --negotiation-restrict"
        )

    have_map = _negotiation_have_map(repo, restrict)
    haves = set(have_map)
    effective_include = _effective_negotiation_includes(repo, source, include)
    if effective_include:
        haves.update(plan_included_haves(repo, effective_include))

    client = SmartHttpV2FetchClient(_source_url(repo, source))
    advertisement = client.discover_refs()
    if advertisement is None:
        raise RuntimeError("--negotiate-only requires protocol version 2")
    common = client.negotiate(haves=haves, advertisement=advertisement)
    if common is None:
        raise RuntimeError("--negotiate-only requires protocol version 2")

    # Includes can help negotiation but current Git defines the printed domain
    # in terms of ancestors reachable from the restriction/tip arguments.
    return [have_map[oid] for oid in common if oid in have_map]
