"""Compose protocol-v2 object filtering with time/revision shallow cutoffs."""

from __future__ import annotations

from typing import Iterable, Optional, Sequence

from .protocol_v2 import ProtocolV2Capabilities
from .protocol_v2_fetch import (
    V2FetchResult,
    _pkt_line,
    _validate_fetch_response_for_request,
)
from .protocol_v2_filter_fetch import normalize_filter_spec
from .protocol_v2_shallow_cutoff import (
    SmartHttpV2ShallowCutoffClient,
    build_shallow_cutoff_fetch_request,
    validate_shallow_response_for_request,
)
from .remote import Advertisement, PackParser


def build_filtered_shallow_cutoff_fetch_request(
    capabilities: ProtocolV2Capabilities,
    wants: Sequence[str],
    filter_spec: str,
    *,
    haves: Iterable[str] = (),
    no_progress: bool = True,
    ofs_delta: bool = True,
    include_tag: bool = False,
    shallow: Iterable[str] = (),
    deepen_since: Optional[int] = None,
    deepen_not: Sequence[str] = (),
    server_options: Sequence[str] = (),
) -> bytes:
    """Build one terminating fetch with both a shallow cutoff and object filter.

    Phase313 remains authoritative for shallow/time/revision validation and
    request layout through the last have. Phase312 remains authoritative for
    filter-spec validation and normalization. This integration layer only joins
    those exact-green primitives and appends the terminating ``filter``/``done``
    tail.
    """

    if not capabilities.supports("fetch"):
        raise RuntimeError("Remote protocol-v2 server does not advertise fetch")
    if not capabilities.feature("fetch", "filter"):
        raise RuntimeError("Remote protocol-v2 fetch does not advertise filter")

    normalized = normalize_filter_spec(filter_spec)
    body = build_shallow_cutoff_fetch_request(
        capabilities,
        wants,
        haves=haves,
        done=False,
        no_progress=no_progress,
        ofs_delta=ofs_delta,
        include_tag=include_tag,
        shallow=shallow,
        deepen_since=deepen_since,
        deepen_not=deepen_not,
        server_options=server_options,
    )
    if not body.endswith(b"0000"):
        raise AssertionError("protocol-v2 shallow cutoff builder did not return a flush packet")

    return (
        body[:-4]
        + _pkt_line(f"filter {normalized}\n".encode("utf-8"))
        + _pkt_line(b"done\n")
        + b"0000"
    )


class SmartHttpV2FilteredShallowClient(SmartHttpV2ShallowCutoffClient):
    """Smart HTTP client composing partial-fetch filters with shallow cutoffs."""

    def fetch_filtered_shallow(
        self,
        filter_spec: str,
        *,
        deepen_since: Optional[int] = None,
        deepen_not: Sequence[str] = (),
        haves: Optional[Iterable[str]] = None,
        advertisement: Optional[Advertisement] = None,
        shallow: Iterable[str] = (),
    ) -> Optional[V2FetchResult]:
        capabilities = self.discover_capabilities()
        if capabilities is None:
            return None
        if not capabilities.supports("fetch"):
            raise RuntimeError("Remote protocol-v2 server does not advertise fetch")

        advertisement = advertisement or self._discover_refs_with_capabilities(capabilities)
        wants = self._wants(advertisement)
        if not wants:
            raise RuntimeError("Remote repository does not advertise any refs.")

        shallow_oids = tuple(shallow)
        body = build_filtered_shallow_cutoff_fetch_request(
            capabilities,
            wants,
            filter_spec,
            haves=haves or (),
            shallow=shallow_oids,
            deepen_since=deepen_since,
            deepen_not=deepen_not,
            server_options=self.server_options,
        )
        parsed = self._post_fetch(body)
        _validate_fetch_response_for_request(parsed, done=True)
        validate_shallow_response_for_request(
            parsed,
            requested_shallow=shallow_oids,
        )
        assert parsed.pack is not None

        return V2FetchResult(
            advertisement,
            PackParser(parsed.pack).parse(),
            parsed.shallow,
            parsed.unshallow,
        )
