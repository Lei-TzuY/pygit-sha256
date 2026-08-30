"""Protocol-v2 ``ls-refs`` helpers that preserve explicit unborn metadata.

The core ``protocol_v2`` module intentionally returns the long-standing
``Advertisement`` shape.  That shape carries concrete refs and symrefs but has
no channel for the protocol-v2 ``unborn`` sentinel.  This additive layer keeps
that public API stable while exposing the server's explicit unborn ref names to
callers that need to distinguish an empty remote HEAD from a merely absent ref.
"""

from __future__ import annotations

import urllib.request
from dataclasses import dataclass
from typing import FrozenSet, Optional, Sequence

from .protocol_v2 import (
    ProtocolV2Capabilities,
    SmartHttpV2QueryClient,
    _UPLOAD_PACK_REQUEST_MEDIA_TYPE,
    _UPLOAD_PACK_RESULT_MEDIA_TYPE,
    _payload_without_optional_lf,
    _read_packet,
    _validate_smart_http_content_type,
    build_ls_refs_request,
    parse_ls_refs_response,
)
from .remote import Advertisement


@dataclass(frozen=True)
class ProtocolV2LsRefsResult:
    """One validated ``ls-refs`` result plus explicit unborn ref names."""

    advertisement: Advertisement
    unborn: FrozenSet[str]


def _extract_unborn_refs(
    data: bytes,
    capabilities: ProtocolV2Capabilities,
    advertisement: Advertisement,
) -> FrozenSet[str]:
    """Extract and validate protocol-v2 ``unborn`` records.

    ``parse_ls_refs_response`` remains authoritative for the generic pkt-line,
    UTF-8, SHA-1, duplicate-record, symref, and peeled grammar.  This second pass
    deliberately validates only semantics that are otherwise lost when the
    ``unborn`` sentinel is omitted from the historical ``Advertisement`` shape.
    """

    unborn: set[str] = set()
    offset = 0
    while offset < len(data):
        kind, payload, offset = _read_packet(data, offset)
        if kind == "flush":
            break
        if kind != "data" or payload is None:
            # The shared parser has already rejected invalid framing.  Keep this
            # guard defensive in case this helper is reused independently later.
            raise ValueError("Unexpected packet in protocol-v2 ls-refs response")

        raw = _payload_without_optional_lf(
            payload,
            context="protocol-v2 ls-refs record",
        )
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Invalid UTF-8 in protocol-v2 ls-refs response") from exc
        fields = text.split(" ")
        if len(fields) < 2:
            raise ValueError("Malformed protocol-v2 ls-refs response line")

        oid_or_unborn, name, *attributes = fields
        if oid_or_unborn != "unborn":
            continue

        if not capabilities.feature("ls-refs", "unborn"):
            raise ValueError(
                "protocol-v2 ls-refs returned unborn without advertising the unborn feature"
            )
        if name != "HEAD":
            raise ValueError(
                "protocol-v2 ls-refs unborn record must describe HEAD"
            )
        if name not in advertisement.symrefs:
            raise ValueError(
                "protocol-v2 unborn HEAD is missing symref-target metadata"
            )
        if any(attribute.startswith("peeled:") for attribute in attributes):
            raise ValueError("protocol-v2 unborn HEAD cannot carry peeled metadata")

        unborn.add(name)

    return frozenset(unborn)


def parse_ls_refs_response_with_unborn(
    data: bytes,
    capabilities: ProtocolV2Capabilities,
) -> ProtocolV2LsRefsResult:
    """Parse ``ls-refs`` while preserving explicit remote unborn state."""

    advertisement = parse_ls_refs_response(data, capabilities)
    unborn = _extract_unborn_refs(data, capabilities, advertisement)
    return ProtocolV2LsRefsResult(advertisement, unborn)


class SmartHttpV2UnbornQueryClient(SmartHttpV2QueryClient):
    """Smart-HTTP v2 ref discovery that retains unborn metadata."""

    def discover_refs_with_unborn(
        self,
        *,
        prefixes: Sequence[str] = (),
    ) -> Optional[ProtocolV2LsRefsResult]:
        """Return refs plus unborn names, or ``None`` for a protocol-v0 fallback."""

        capabilities = self.discover_capabilities()
        if capabilities is None:
            return None

        body = build_ls_refs_request(
            capabilities,
            prefixes=prefixes,
            server_options=self.server_options,
        )
        request = urllib.request.Request(
            f"{self.url}/git-upload-pack",
            data=body,
            method="POST",
            headers={
                "Accept": _UPLOAD_PACK_RESULT_MEDIA_TYPE,
                "Content-Type": _UPLOAD_PACK_REQUEST_MEDIA_TYPE,
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            _validate_smart_http_content_type(
                response,
                _UPLOAD_PACK_RESULT_MEDIA_TYPE,
                context="upload-pack response",
            )
            return parse_ls_refs_response_with_unborn(
                response.read(),
                capabilities,
            )
