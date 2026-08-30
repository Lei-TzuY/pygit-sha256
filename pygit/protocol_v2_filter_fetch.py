"""Protocol-v2 filtered fetch support.

This module layers the ``filter <filter-spec>`` request feature on top of the
strict Phase309 protocol-v2 fetch transport.  It deliberately does not persist
promisor metadata or materialize omitted objects: this is the wire-level partial
fetch primitive only.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional, Sequence

from .protocol_v2 import ProtocolV2Capabilities
from .protocol_v2_fetch import (
    SmartHttpV2FetchClient,
    V2FetchResult,
    _pkt_line,
    _validate_fetch_response_for_request,
    build_fetch_request,
)
from .remote import Advertisement, FetchResult, PackParser


_BLOB_LIMIT_RE = re.compile(r"blob:limit=([0-9]+)([kmg]?)\Z")
_SCALE = {"": 1, "k": 1024, "m": 1024 * 1024, "g": 1024 * 1024 * 1024}


def normalize_filter_spec(filter_spec: str) -> str:
    """Validate one transport filter-spec and normalize scaled blob limits.

    The protocol delegates filter syntax to ``rev-list`` and can grow new filter
    kinds over time, so this boundary intentionally does not whitelist the
    current set of filter names.  It rejects framing-unsafe whitespace/control
    characters, while otherwise forwarding future syntactically safe specs.

    Git recommends that senders expand scaled ``blob:limit`` values before
    communicating with older receivers.  Apply that interoperability rule here.
    """

    if not isinstance(filter_spec, str):
        raise TypeError("protocol-v2 filter spec must be a string")
    if not filter_spec:
        raise ValueError("protocol-v2 filter spec must not be empty")
    if any(character.isspace() for character in filter_spec):
        raise ValueError("protocol-v2 filter spec must not contain whitespace")
    if "\x00" in filter_spec or "\r" in filter_spec or "\n" in filter_spec:
        raise ValueError("protocol-v2 filter spec contains an invalid control character")

    match = _BLOB_LIMIT_RE.fullmatch(filter_spec)
    if filter_spec.startswith("blob:limit=") and match is None:
        raise ValueError("malformed protocol-v2 blob:limit filter spec")
    if match is not None:
        amount = int(match.group(1)) * _SCALE[match.group(2)]
        filter_spec = f"blob:limit={amount}"

    payload = f"filter {filter_spec}\n".encode("utf-8")
    if len(payload) + 4 > 0xFFFF:
        raise ValueError("protocol-v2 filter spec is too large for one pkt-line")
    return filter_spec


def build_filtered_fetch_request(
    capabilities: ProtocolV2Capabilities,
    wants: Sequence[str],
    filter_spec: str,
    *,
    haves: Iterable[str] = (),
    no_progress: bool = True,
    ofs_delta: bool = True,
    include_tag: bool = False,
    shallow: Iterable[str] = (),
    deepen: Optional[int] = None,
    deepen_relative: bool = False,
    server_options: Sequence[str] = (),
) -> bytes:
    """Build one terminating protocol-v2 fetch with exactly one object filter.

    OID, shallow, server-option, and command framing validation stays delegated
    to the Phase309 ordinary fetch builder.  This helper only gates and frames
    the optional protocol-v2 ``filter`` feature, then terminates negotiation with
    ``done`` so the response must contain a packfile.
    """

    if not capabilities.supports("fetch"):
        raise RuntimeError("Remote protocol-v2 server does not advertise fetch")
    if not capabilities.feature("fetch", "filter"):
        raise RuntimeError("Remote protocol-v2 fetch does not advertise filter")

    normalized = normalize_filter_spec(filter_spec)
    body = build_fetch_request(
        capabilities,
        wants,
        haves=haves,
        done=False,
        no_progress=no_progress,
        ofs_delta=ofs_delta,
        include_tag=include_tag,
        shallow=shallow,
        deepen=deepen,
        deepen_relative=deepen_relative,
        server_options=server_options,
    )
    if not body.endswith(b"0000"):
        raise AssertionError("protocol-v2 fetch builder did not return a flush packet")

    return (
        body[:-4]
        + _pkt_line(f"filter {normalized}\n".encode("utf-8"))
        + _pkt_line(b"done\n")
        + b"0000"
    )


class SmartHttpV2FilterFetchClient(SmartHttpV2FetchClient):
    """Smart-HTTP client for one-shot protocol-v2 partial fetches."""

    def fetch_filtered(
        self,
        filter_spec: str,
        haves: Optional[Iterable[str]] = None,
        advertisement: Optional[Advertisement] = None,
        *,
        shallow: Iterable[str] = (),
        deepen: Optional[int] = None,
        deepen_relative: bool = False,
    ) -> Optional[FetchResult]:
        """Fetch a filtered native pack without mutating persistent promisor state.

        Return ``None`` only when capability discovery indicates that the server
        did not enter protocol v2, matching the existing v2 client fallback
        contract.  Omitted objects are intentionally not invented, mapped, or
        fetched here; callers that need promisor persistence must do so at the
        repository layer with genuine remote-native identities.
        """

        capabilities = self.discover_capabilities()
        if capabilities is None:
            return None
        if not capabilities.supports("fetch"):
            raise RuntimeError("Remote protocol-v2 server does not advertise fetch")

        advertisement = advertisement or self._discover_refs_with_capabilities(
            capabilities
        )
        wants = self._wants(advertisement)
        if not wants:
            raise RuntimeError("Remote repository does not advertise any refs.")

        normalized = normalize_filter_spec(filter_spec)
        body = build_filtered_fetch_request(
            capabilities,
            wants,
            normalized,
            haves=haves or (),
            shallow=shallow,
            deepen=deepen,
            deepen_relative=deepen_relative,
            server_options=self.server_options,
        )
        parsed = self._post_fetch(body)
        _validate_fetch_response_for_request(parsed, done=True)
        assert parsed.pack is not None

        return V2FetchResult(
            advertisement,
            PackParser(parsed.pack).parse(),
            parsed.shallow,
            parsed.unshallow,
        )
