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


def build_object_info_size_request(
    capabilities: ProtocolV2Capabilities,
    oids: Sequence[str],
    *,
    server_options: Sequence[str] = (),
) -> bytes:
    """Build one capability-gated ``object-info`` size request."""

    if not capabilities.supports("object-info"):
        raise RuntimeError("Remote protocol-v2 server does not advertise object-info")

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
    """Parse the ``size`` attribute header followed by per-OID results."""

    saw_size = False
    saw_object = False
    seen: set[str] = set()
    results = []
    offset = 0

    while offset < len(data):
        kind, payload, offset = _read_packet(data, offset)
        if kind in {"flush", "response-end"}:
            break
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
    return tuple(results)


class SmartHttpV2ObjectInfoClient(SmartHttpV2QueryClient):
    """Capability-gated smart-HTTP protocol-v2 object-size client."""

    def _post_object_info(self, body: bytes) -> Tuple[ObjectSizeInfo, ...]:
        request = urllib.request.Request(
            f"{self.url}/git-upload-pack",
            data=body,
            method="POST",
            headers={
                "Accept": "application/x-git-upload-pack-result",
                "Content-Type": "application/x-git-upload-pack-request",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
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
        capabilities = self.discover_capabilities()
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
