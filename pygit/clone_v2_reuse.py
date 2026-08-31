"""Reuse one unborn-aware clone discovery for the terminating v2 fetch.

Phase358 keeps clone discovery single-shot without teaching the generic fetch
client about clone-specific unborn metadata.  Request construction and response
parsing remain delegated to the established protocol-v2 fetch implementation.
"""

from __future__ import annotations

from typing import Iterable, Optional

from .clone_unborn import CloneRefDiscovery
from .fetch_partial import _build_filtered_fetch_request, _filtered_v2_fetch
from .protocol_v2_fetch import (
    SmartHttpV2FetchClient,
    V2FetchResult,
    _validate_fetch_response_for_request,
    build_fetch_request,
)
from .remote import PackParser


def fetch_discovered_clone(
    client: SmartHttpV2FetchClient,
    discovery: CloneRefDiscovery,
    *,
    haves: Iterable[str] = (),
    deepen: Optional[int] = None,
) -> V2FetchResult:
    """Fetch using the exact capabilities/refs already discovered for clone.

    Older test doubles may not expose the retained capability sidecar.  In that
    compatibility case delegate to the historical client method, which performs
    its own capability lookup while still reusing the supplied advertisement.
    """

    advertisement = discovery.refs.advertisement
    capabilities = discovery.capabilities
    if capabilities is None:
        result = client.fetch(
            haves=haves,
            advertisement=advertisement,
            deepen=deepen,
        )
        if result is None:
            raise RuntimeError("clone fetch requires protocol version 2")
        return result

    if not capabilities.supports("fetch"):
        raise RuntimeError("Remote protocol-v2 server does not advertise fetch")
    wants = client._wants(advertisement)
    if not wants:
        raise RuntimeError("Remote repository does not advertise any refs.")

    body = build_fetch_request(
        capabilities,
        wants,
        haves=haves,
        done=True,
        deepen=deepen,
        server_options=client.server_options,
    )
    parsed = client._post_fetch(body)
    _validate_fetch_response_for_request(parsed, done=True)
    return V2FetchResult(
        advertisement,
        PackParser(parsed.pack).parse(),
        parsed.shallow,
        parsed.unshallow,
    )


def fetch_filtered_discovered_clone(
    client: SmartHttpV2FetchClient,
    discovery: CloneRefDiscovery,
    *,
    haves: Iterable[str] = (),
    filter_spec: str,
) -> V2FetchResult:
    """Run one filtered clone fetch from an already validated discovery."""

    advertisement = discovery.refs.advertisement
    capabilities = discovery.capabilities
    if capabilities is None:
        return _filtered_v2_fetch(
            client,
            haves=haves,
            advertisement=advertisement,
            filter_spec=filter_spec,
        )

    if not capabilities.supports("fetch"):
        raise RuntimeError("Remote protocol-v2 server does not advertise fetch")
    wants = client._wants(advertisement)
    if not wants:
        raise RuntimeError("Remote repository does not advertise any refs.")

    body = _build_filtered_fetch_request(
        capabilities,
        wants,
        haves=haves,
        filter_spec=filter_spec,
        server_options=client.server_options,
    )
    parsed = client._post_fetch(body)
    if parsed.pack is None:
        raise ValueError("protocol-v2 filtered fetch response did not contain a packfile")
    _validate_fetch_response_for_request(parsed, done=True)
    return V2FetchResult(
        advertisement,
        PackParser(parsed.pack).parse(),
        parsed.shallow,
        parsed.unshallow,
    )
