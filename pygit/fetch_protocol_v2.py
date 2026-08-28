"""Command-scoped protocol-v2 fetch transport and negotiate-only support."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Dict, Iterator, Optional, Sequence
from urllib.parse import urlsplit

from .fetch_negotiation import (
    plan_included_haves,
    reachable_commits,
    resolve_negotiation_tips,
)
from .protocol_v2 import ProtocolV2Unavailable, SmartHttpV2FetchClient
from .remote import NativeExporter, SmartHttpClient
from .remote_urls import fetch_url
from .repo import Repository


def protocol_v2_requested(repo: Optional[Repository]) -> bool:
    """Return whether the repository explicitly prefers protocol v2.

    Fetch wrapper regressions use lightweight stand-ins for a repository when
    exercising command-scope composition. Protocol preference is optional, so a
    stand-in without ``config_get`` must remain transparent rather than making
    an otherwise unrelated fetch path fail.
    """
    if repo is None:
        return False
    getter = getattr(repo, "config_get", None)
    return callable(getter) and getter("protocol", "version") == "2"


@contextmanager
def protocol_v2_transport() -> Iterator[None]:
    """Temporarily make existing SmartHttpClient users prefer protocol v2.

    The established fetch stack constructs ``SmartHttpClient`` directly in
    several modules. Replacing its two transport methods command-locally keeps
    every higher-level refspec/prune/tag/atomic path centralized. Servers that
    ignore the v2 handshake fall back to the original protocol-v0 methods.
    """

    original_discover = SmartHttpClient.discover
    original_fetch = SmartHttpClient.fetch
    clients: Dict[str, SmartHttpV2FetchClient] = {}
    fallback: set[str] = set()

    def client_for(instance: SmartHttpClient) -> SmartHttpV2FetchClient:
        client = clients.get(instance.url)
        if client is None:
            client = SmartHttpV2FetchClient(instance.url, timeout=instance.timeout)
            clients[instance.url] = client
        return client

    def discover(self: SmartHttpClient):
        if self.url in fallback:
            return original_discover(self)
        client = client_for(self)
        try:
            return client.discover()
        except ProtocolV2Unavailable:
            fallback.add(self.url)
            return original_discover(self)

    def fetch(self: SmartHttpClient, haves=None, advertisement=None):
        if self.url in fallback:
            return original_fetch(self, haves=haves, advertisement=advertisement)
        client = client_for(self)
        try:
            return client.fetch(haves=haves, advertisement=advertisement)
        except ProtocolV2Unavailable:
            fallback.add(self.url)
            return original_fetch(self, haves=haves, advertisement=advertisement)

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
    """Map native SHA-1 have identities back to local SHA-256 commits."""
    tips = resolve_negotiation_tips(repo, expressions)
    commits = reachable_commits(repo, tips)
    known: Dict[str, str] = {}
    for remote in repo.list_remotes():
        known.update(repo._read_native_map(remote))
    exporter = NativeExporter(repo.store, known_oids=known, have_shas=set(known))
    return {exporter.export_oid(oid): oid for oid in commits}


def negotiate_only(
    repo: Repository,
    *,
    source: str,
    restrict: Sequence[str],
    include: Sequence[str] = (),
) -> Sequence[str]:
    """Return local SHA-256 commits known in common with a v2 server.

    ``--negotiate-only`` is intentionally strict: unlike ordinary v2-preferred
    fetch it cannot fall back to protocol v0, because the command is defined in
    terms of protocol-v2 negotiation. ``wait-for-done`` is required so the
    server cannot decide to send a packfile while the client is only asking for
    common ancestors.
    """

    if not restrict:
        raise RuntimeError(
            "--negotiate-only requires at least one --negotiation-restrict"
        )

    have_map = _negotiation_have_map(repo, restrict)
    haves = set(have_map)
    if include:
        haves.update(plan_included_haves(repo, include))

    client = SmartHttpV2FetchClient(_source_url(repo, source))
    try:
        advertisement = client.discover()
    except ProtocolV2Unavailable as exc:
        raise RuntimeError("--negotiate-only requires protocol version 2") from exc

    common = client.negotiate(haves=haves, advertisement=advertisement)
    # The documented output is the common subset of ancestors from the
    # restriction tips. Include-only haves may influence negotiation but are
    # not part of that output domain.
    return [have_map[oid] for oid in common if oid in have_map]
