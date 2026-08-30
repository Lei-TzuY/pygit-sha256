"""Protocol-v2 packfile URI negotiation and descriptor parsing.

Phase318 layers packfile-URI support on top of the exact-green Phase316 fetch
transport without teaching repository code to download arbitrary external URIs.
The transport can request URI offload, normalize Git's ``sideband-all`` response
form, validate URI descriptors, and expose them to a higher-level downloader.
"""

from __future__ import annotations

import urllib.request
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence, Tuple

from .protocol_v2 import (
    ProtocolV2Capabilities,
    _UPLOAD_PACK_REQUEST_MEDIA_TYPE,
    _UPLOAD_PACK_RESULT_MEDIA_TYPE,
    _read_packet,
    _validate_smart_http_content_type,
)
from .protocol_v2_fetch import (
    ProtocolV2FetchResponse,
    SmartHttpV2FetchClient,
    V2FetchResult,
    _pkt_line,
    _validate_fetch_response_for_request,
    build_fetch_request,
    parse_fetch_response,
)
from .remote import Advertisement, FetchResult, PackParser


_SUPPORTED_URI_PROTOCOLS = frozenset({"http", "https"})


@dataclass(frozen=True)
class PackfileUriDescriptor:
    """One remote pack descriptor from a ``packfile-uris`` section.

    ``pack_hash`` is intentionally the 40-hex pack checksum defined by the
    current protocol-v2 grammar.  It is transport metadata, not a local pygit
    object identity.  ``uri`` stays as bytes because the protocol permits the
    URI field to contain octets above ASCII and does not require UTF-8.
    """

    pack_hash: str
    uri: bytes

    @property
    def scheme(self) -> str:
        prefix, separator, _ = self.uri.partition(b":")
        if not separator:
            raise ValueError("protocol-v2 packfile URI is missing a scheme")
        try:
            return prefix.decode("ascii").lower()
        except UnicodeDecodeError as exc:
            raise ValueError("protocol-v2 packfile URI scheme must be ASCII") from exc


@dataclass(frozen=True)
class ProtocolV2PackfileUriResponse:
    """A normal parsed fetch response plus external pack descriptors."""

    fetch: ProtocolV2FetchResponse
    packfile_uris: Tuple[PackfileUriDescriptor, ...]
    sideband_all: bool = False


@dataclass
class V2PackfileUriFetchResult(V2FetchResult):
    """Fetch result exposing packs that remain to be retrieved externally."""

    packfile_uris: Tuple[PackfileUriDescriptor, ...] = ()


def normalize_packfile_uri_protocols(protocols: Sequence[str]) -> Tuple[str, ...]:
    """Return ordered, unique, currently-supported packfile URI schemes."""

    if not protocols:
        raise ValueError("protocol-v2 packfile-uris requires at least one protocol")

    normalized = []
    seen = set()
    for protocol in protocols:
        if not isinstance(protocol, str):
            raise TypeError("protocol-v2 packfile URI protocol must be a string")
        value = protocol.lower()
        if value not in _SUPPORTED_URI_PROTOCOLS:
            raise ValueError(
                f"unsupported protocol-v2 packfile URI protocol: {protocol!r}"
            )
        if value in seen:
            raise ValueError(f"duplicate protocol-v2 packfile URI protocol: {value}")
        seen.add(value)
        normalized.append(value)
    return tuple(normalized)


def build_packfile_uri_fetch_request(
    capabilities: ProtocolV2Capabilities,
    wants: Sequence[str],
    protocols: Sequence[str],
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
    """Build one terminating fetch that allows the server to offload packs.

    Native Git emits a non-empty ``packfile-uris`` response only when
    ``sideband-all`` is also negotiated, so request it opportunistically when
    the server advertises that feature.  Servers without ``sideband-all`` may
    still accept the URI request and return the ordinary inline pack.
    """

    if not capabilities.supports("fetch"):
        raise RuntimeError("Remote protocol-v2 server does not advertise fetch")
    if not capabilities.feature("fetch", "packfile-uris"):
        raise RuntimeError(
            "Remote protocol-v2 fetch does not advertise packfile-uris"
        )

    requested = normalize_packfile_uri_protocols(protocols)
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

    extra = b""
    if capabilities.feature("fetch", "sideband-all"):
        extra += _pkt_line(b"sideband-all\n")
    extra += _pkt_line(
        f"packfile-uris {','.join(requested)}\n".encode("ascii")
    )
    extra += _pkt_line(b"done\n")
    return body[:-4] + extra + b"0000"


def _decode_text_record(payload: bytes, *, context: str) -> bytes:
    """Validate zero-or-one terminal LF without imposing UTF-8 on URI bytes."""

    if payload.endswith(b"\n"):
        payload = payload[:-1]
    if b"\n" in payload:
        raise ValueError(f"Unexpected LF inside {context}")
    if b"\r" in payload:
        raise ValueError(f"Unexpected CR inside {context}")
    if b"\x00" in payload:
        raise ValueError(f"Unexpected NUL inside {context}")
    return payload


def _header(payload: bytes) -> str:
    record = _decode_text_record(payload, context="protocol-v2 fetch section header")
    try:
        return record.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("Invalid ASCII in protocol-v2 fetch section header") from exc


def _normalize_sideband_all(data: bytes) -> Tuple[bytes, bool]:
    """Convert a sideband-all response into the ordinary fetch parser shape.

    With ``sideband-all``, Git prefixes section headers and textual records with
    channel 1 as well as pack bytes.  The established fetch parser expects only
    packfile payload records to retain their sideband byte, so this adapter
    removes the global envelope while re-adding channel 1 inside ``packfile``.
    Channel 2 progress is discarded and channel 3 remains a fatal server error.
    """

    offset = 0
    first_data_payload: Optional[bytes] = None
    while offset < len(data):
        kind, payload, offset = _read_packet(data, offset)
        if kind == "data":
            first_data_payload = payload
            break
        if kind in {"flush", "response-end"}:
            break

    sideband_all = bool(
        first_data_payload
        and first_data_payload[0] in (1, 2, 3)
    )
    if not sideband_all:
        return data, False

    out = bytearray()
    offset = 0
    section: Optional[str] = None
    expect_header = True

    while offset < len(data):
        kind, payload, offset = _read_packet(data, offset)
        if kind == "flush":
            out += b"0000"
            continue
        if kind == "delim":
            out += b"0001"
            section = None
            expect_header = True
            continue
        if kind == "response-end":
            out += b"0002"
            continue
        if kind != "data" or payload is None or not payload:
            raise ValueError("Malformed protocol-v2 sideband-all packet")

        channel = payload[0]
        body = payload[1:]
        if channel == 2:
            continue
        if channel == 3:
            raise RuntimeError(body.decode("utf-8", errors="replace").strip())
        if channel != 1:
            raise ValueError(f"Invalid protocol-v2 sideband-all channel: {channel}")

        if expect_header:
            section = _header(body)
            out += _pkt_line(body)
            expect_header = False
        elif section == "packfile":
            out += _pkt_line(b"\x01" + body)
        else:
            out += _pkt_line(body)

    return bytes(out), True


def _parse_packfile_uri_descriptor(payload: bytes) -> PackfileUriDescriptor:
    record = _decode_text_record(payload, context="protocol-v2 packfile URI record")
    hash_bytes, separator, uri = record.partition(b" ")
    if not separator or len(hash_bytes) != 40:
        raise ValueError("Malformed protocol-v2 packfile URI descriptor")
    try:
        pack_hash = hash_bytes.decode("ascii").lower()
        int(pack_hash, 16)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError(
            "protocol-v2 packfile URI hash must be a 40-hex pack checksum"
        ) from exc
    if not uri:
        raise ValueError("protocol-v2 packfile URI must not be empty")
    if any(byte < 0x20 for byte in uri):
        raise ValueError("protocol-v2 packfile URI contains an invalid control byte")
    return PackfileUriDescriptor(pack_hash, uri)


def _strip_packfile_uri_section(
    data: bytes,
) -> Tuple[bytes, Tuple[PackfileUriDescriptor, ...]]:
    """Extract the optional URI section and return a base-parser response."""

    offset = 0
    section: Optional[str] = None
    uri_start: Optional[int] = None
    descriptors = []
    seen_hashes = set()

    while offset < len(data):
        packet_start = offset
        kind, payload, offset = _read_packet(data, offset)

        if kind == "data":
            if payload is None:
                raise ValueError("Malformed protocol-v2 fetch packet")
            if section is None:
                section = _header(payload)
                if section == "packfile-uris":
                    if uri_start is not None:
                        raise ValueError("Duplicate protocol-v2 packfile-uris section")
                    uri_start = packet_start
            elif section == "packfile-uris":
                descriptor = _parse_packfile_uri_descriptor(payload)
                if descriptor.pack_hash in seen_hashes:
                    raise ValueError(
                        f"Duplicate protocol-v2 packfile URI hash: {descriptor.pack_hash}"
                    )
                seen_hashes.add(descriptor.pack_hash)
                descriptors.append(descriptor)
            continue

        if kind == "delim":
            if section == "packfile-uris":
                if uri_start is None or not descriptors:
                    raise ValueError(
                        "protocol-v2 packfile-uris section contained no descriptors"
                    )
                next_kind, next_payload, _ = _read_packet(data, offset)
                if (
                    next_kind != "data"
                    or next_payload is None
                    or _header(next_payload) != "packfile"
                ):
                    raise ValueError(
                        "protocol-v2 packfile-uris section must directly precede packfile"
                    )
                cleaned = data[:uri_start] + data[offset:]
                return cleaned, tuple(descriptors)
            section = None
            continue

        if kind == "flush":
            if section == "packfile-uris":
                raise ValueError(
                    "protocol-v2 packfile-uris section must be followed by packfile"
                )
            break

    return data, ()


def parse_fetch_response_with_packfile_uris(
    data: bytes,
) -> ProtocolV2PackfileUriResponse:
    """Parse an ordinary or sideband-all fetch with optional packfile URIs."""

    normalized, sideband_all = _normalize_sideband_all(data)
    base_data, descriptors = _strip_packfile_uri_section(normalized)
    fetch = parse_fetch_response(base_data)
    return ProtocolV2PackfileUriResponse(fetch, descriptors, sideband_all)


def validate_packfile_uri_response(
    response: ProtocolV2PackfileUriResponse,
    requested_protocols: Sequence[str],
) -> None:
    """Apply request-aware packfile URI scheme and terminating-fetch checks."""

    requested = set(normalize_packfile_uri_protocols(requested_protocols))
    _validate_fetch_response_for_request(response.fetch, done=True)
    for descriptor in response.packfile_uris:
        if descriptor.scheme not in requested:
            raise ValueError(
                "protocol-v2 packfile URI response used an unrequested protocol: "
                f"{descriptor.scheme}"
            )


class SmartHttpV2PackfileUriClient(SmartHttpV2FetchClient):
    """Smart-HTTP fetch client that returns external pack descriptors safely."""

    def _post_packfile_uri_fetch(self, body: bytes) -> ProtocolV2PackfileUriResponse:
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
            return parse_fetch_response_with_packfile_uris(response.read())

    def fetch_with_packfile_uris(
        self,
        protocols: Sequence[str],
        haves: Optional[Iterable[str]] = None,
        advertisement: Optional[Advertisement] = None,
        *,
        shallow: Iterable[str] = (),
        deepen: Optional[int] = None,
        deepen_relative: bool = False,
    ) -> Optional[FetchResult]:
        """Fetch the inline pack and return, but do not download, external packs."""

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

        requested = normalize_packfile_uri_protocols(protocols)
        body = build_packfile_uri_fetch_request(
            capabilities,
            wants,
            requested,
            haves=haves or (),
            shallow=shallow,
            deepen=deepen,
            deepen_relative=deepen_relative,
            server_options=self.server_options,
        )
        parsed = self._post_packfile_uri_fetch(body)
        validate_packfile_uri_response(parsed, requested)
        assert parsed.fetch.pack is not None

        return V2PackfileUriFetchResult(
            advertisement,
            PackParser(parsed.fetch.pack).parse(),
            parsed.fetch.shallow,
            parsed.fetch.unshallow,
            parsed.packfile_uris,
        )
