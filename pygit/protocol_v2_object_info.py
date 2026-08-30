"""Git protocol-v2 ``object-info`` size-query transport.

``object-info`` is intentionally metadata-only: a supporting server can report
an object's native uncompressed size without sending its contents.  This module
keeps that information at the remote SHA-1 boundary so later partial-clone
phases can persist trustworthy size metadata without inventing local SHA-256
identities or materializing promised objects merely to classify a filter.
"""

from __future__ import annotations

import urllib.request
from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple

from .protocol_v2 import (
    ProtocolV2Capabilities,
    SmartHttpV2QueryClient,
    _command_prefix,
    _read_packet,
)
from .protocol_v2_fetch import _validate_sha1_oid
from .remote import pkt_line


_UPLOAD_PACK_RESULT_MEDIA_TYPE = "application/x-git-upload-pack-result"


class ObjectInfoUnsupportedError(RuntimeError):
    """The remote negotiated protocol v2 but does not advertise object-info.

    This is a stable capability-negative result, not a transport/session
    failure.  Callers may safely retain the negotiated capability advertisement
    instead of discarding the client and rediscovering the same absence on every
    metadata refresh.
    """


@dataclass(frozen=True)
class ObjectSizeInfo:
    """One native object-size result.

    ``size`` is ``None`` only when the remote explicitly reports that the
    requested full OID is unknown.  The OID always remains the native SHA-1
    identity used by the upload-pack protocol boundary.
    """

    oid: str
    size: Optional[int]

    @property
    def exists(self) -> bool:
        return self.size is not None


def _normalize_oids(oids: Sequence[str]) -> Tuple[str, ...]:
    normalized = tuple(
        sorted({_validate_sha1_oid(oid, field="object-info oid") for oid in oids})
    )
    if not normalized:
        raise ValueError("protocol-v2 object-info requires at least one object id")
    return normalized


def _response_content_type(response) -> Optional[str]:
    """Return one normalized HTTP media type when response headers are available.

    Real ``urllib`` responses expose either ``headers`` or ``getheader``.  A few
    older unit-test doubles predate HTTP-envelope validation and expose only
    ``read()``; callers can distinguish that no-header-API case from a genuine
    HTTP response whose Content-Type header is missing.
    """

    headers = getattr(response, "headers", None)
    if headers is not None:
        getter = getattr(headers, "get", None)
        if callable(getter):
            raw = getter("Content-Type")
            if raw is None:
                return None
            return str(raw).split(";", 1)[0].strip().lower()

    getheader = getattr(response, "getheader", None)
    if callable(getheader):
        raw = getheader("Content-Type")
        if raw is None:
            return None
        return str(raw).split(";", 1)[0].strip().lower()

    return None


def _validate_upload_pack_result_content_type(response) -> None:
    """Reject a real smart-HTTP response with the wrong upload-pack media type.

    Git smart HTTP identifies POST results as
    ``application/x-git-upload-pack-result``.  Validate the media type before
    reading/parsing object-info metadata so an HTML proxy/login/error body cannot
    be mistaken for trusted pkt-line metadata.  Parameters and media-type case
    are normalized away.  Header-less legacy test doubles are ignored; actual
    ``urllib`` HTTP responses always expose a header API, including when the
    Content-Type field itself is absent.
    """

    has_header_api = getattr(response, "headers", None) is not None or callable(
        getattr(response, "getheader", None)
    )
    if not has_header_api:
        return

    content_type = _response_content_type(response)
    if content_type != _UPLOAD_PACK_RESULT_MEDIA_TYPE:
        rendered = "<missing>" if content_type is None else content_type
        raise ValueError(
            "Unexpected smart-HTTP upload-pack response Content-Type "
            f"{rendered!r}; expected {_UPLOAD_PACK_RESULT_MEDIA_TYPE!r}"
        )


def build_object_info_size_request(
    capabilities: ProtocolV2Capabilities,
    oids: Sequence[str],
    *,
    server_options: Sequence[str] = (),
) -> bytes:
    """Build one capability-gated ``object-info`` size request."""

    if not capabilities.supports("object-info"):
        raise ObjectInfoUnsupportedError(
            "Remote protocol-v2 server does not advertise object-info"
        )

    requested = _normalize_oids(oids)
    body = _command_prefix(
        "object-info",
        capabilities,
        server_options=server_options,
    )
    body += pkt_line(b"size\n")
    for oid in requested:
        body += pkt_line(f"oid {oid}\n".encode())
    return body + b"0000"


def parse_object_info_size_response(data: bytes) -> Tuple[ObjectSizeInfo, ...]:
    """Parse one complete ``object-info size`` response.

    Git's protocol-v2 grammar defines an ``object-info`` response as its info
    pkt-lines followed by one ``flush-pkt``.  Treat that terminator as part of
    the trusted metadata envelope: truncated responses, response-end/delimiter
    packets, and bytes after the flush are rejected rather than silently
    accepting a prefix of a malformed response.
    """

    saw_size = False
    saw_object = False
    saw_flush = False
    seen: set[str] = set()
    results = []
    offset = 0

    while offset < len(data):
        kind, payload, offset = _read_packet(data, offset)
        if kind == "flush":
            saw_flush = True
            if offset != len(data):
                raise ValueError(
                    "Trailing data after protocol-v2 object-info flush packet"
                )
            break
        if kind in {"delim", "response-end"}:
            raise ValueError(
                "Unexpected non-flush terminator in protocol-v2 object-info response"
            )
        if kind != "data" or payload is None:
            raise ValueError("Unexpected delimiter in protocol-v2 object-info response")

        try:
            text = payload.rstrip(b"\n").decode("ascii")
        except UnicodeDecodeError as exc:
            raise ValueError("Invalid ASCII in protocol-v2 object-info response") from exc

        if text == "size":
            if saw_size:
                raise ValueError("Duplicate size attribute in protocol-v2 object-info response")
            if saw_object:
                raise ValueError("object-info size attribute appeared after object results")
            saw_size = True
            continue

        if not saw_size:
            raise ValueError("protocol-v2 object-info response did not begin with size")
        saw_object = True

        oid, separator, raw_size = text.partition(" ")
        if not separator:
            raise ValueError("Malformed protocol-v2 object-info result")
        oid = _validate_sha1_oid(oid, field="object-info response oid")
        if oid in seen:
            raise ValueError(f"Duplicate protocol-v2 object-info result for {oid}")
        seen.add(oid)

        if raw_size == "":
            size: Optional[int] = None
        else:
            if not raw_size.isascii() or not raw_size.isdecimal():
                raise ValueError(
                    f"Malformed protocol-v2 object-info size for {oid}: {raw_size!r}"
                )
            size = int(raw_size, 10)
        results.append(ObjectSizeInfo(oid, size))

    if not saw_size:
        raise ValueError("protocol-v2 object-info response omitted size attribute")
    if not saw_flush:
        raise ValueError("protocol-v2 object-info response did not end with flush packet")
    return tuple(results)


class SmartHttpV2ObjectInfoClient(SmartHttpV2QueryClient):
    """Capability-gated smart-HTTP protocol-v2 object-size client.

    Capability discovery is stable for the lifetime of one smart-HTTP client and
    is therefore cached after the first successful discovery, including the
    protocol-v0 ``None`` result.  This matters for callers that deliberately split
    a large metadata request into several bounded ``object-info`` commands: each
    chunk still gets its own POST, while the remote advertisement is fetched only
    once.  Discovery exceptions are not cached so a later explicit retry remains
    possible.
    """

    def __init__(
        self,
        url: str,
        timeout: int = 30,
        *,
        server_options: Sequence[str] = (),
    ) -> None:
        super().__init__(url, timeout, server_options=server_options)
        self._object_info_capabilities_loaded = False
        self._object_info_capabilities: Optional[ProtocolV2Capabilities] = None

    def _discover_object_info_capabilities(self) -> Optional[ProtocolV2Capabilities]:
        if not self._object_info_capabilities_loaded:
            capabilities = super().discover_capabilities()
            self._object_info_capabilities = capabilities
            self._object_info_capabilities_loaded = True
        return self._object_info_capabilities

    def _post_object_info(self, body: bytes) -> Tuple[ObjectSizeInfo, ...]:
        request = urllib.request.Request(
            f"{self.url}/git-upload-pack",
            data=body,
            method="POST",
            headers={
                "Accept": _UPLOAD_PACK_RESULT_MEDIA_TYPE,
                "Content-Type": "application/x-git-upload-pack-request",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            _validate_upload_pack_result_content_type(response)
            return parse_object_info_size_response(response.read())

    def query_sizes(
        self,
        oids: Sequence[str],
    ) -> Optional[Dict[str, Optional[int]]]:
        """Return native object sizes, or ``None`` when the server is protocol v0.

        A protocol-v2 server that does not advertise ``object-info`` is an
        explicit unsupported capability, not a v0 fallback.  Unknown OIDs are
        retained in the returned mapping with a ``None`` value.
        """

        requested = _normalize_oids(oids)
        capabilities = self._discover_object_info_capabilities()
        if capabilities is None:
            return None

        body = build_object_info_size_request(
            capabilities,
            requested,
            server_options=self.server_options,
        )
        results = self._post_object_info(body)
        by_oid = {item.oid: item.size for item in results}
        if set(by_oid) != set(requested):
            missing = sorted(set(requested) - set(by_oid))
            extra = sorted(set(by_oid) - set(requested))
            raise ValueError(
                "protocol-v2 object-info response did not match requested OIDs"
                f" (missing={missing}, extra={extra})"
            )
        return {oid: by_oid[oid] for oid in requested}
