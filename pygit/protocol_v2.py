"""Read-only Git protocol-v2 smart HTTP discovery and ``ls-refs``.

This module deliberately keeps remote-native object identities at the smart HTTP
boundary. Repository-visible identity remains SHA-256 throughout pygit.
"""

from __future__ import annotations

import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Set

from .remote import Advertisement, pkt_line


_UPLOAD_PACK_ADVERTISEMENT_MEDIA_TYPE = "application/x-git-upload-pack-advertisement"
_UPLOAD_PACK_REQUEST_MEDIA_TYPE = "application/x-git-upload-pack-request"
_UPLOAD_PACK_RESULT_MEDIA_TYPE = "application/x-git-upload-pack-result"


def _read_packet(data: bytes, offset: int) -> tuple[str, Optional[bytes], int]:
    if offset + 4 > len(data):
        raise ValueError("Truncated protocol-v2 pkt-line length")
    raw = data[offset : offset + 4]
    try:
        size = int(raw, 16)
    except ValueError as exc:
        raise ValueError(f"Invalid protocol-v2 pkt-line length: {raw!r}") from exc
    offset += 4
    if size == 0:
        return "flush", None, offset
    if size == 1:
        return "delim", None, offset
    if size == 2:
        return "response-end", None, offset
    if size < 4 or offset + size - 4 > len(data):
        raise ValueError("Truncated protocol-v2 pkt-line payload")
    return "data", data[offset : offset + size - 4], offset + size - 4


def _response_has_header_api(response) -> bool:
    """Return whether *response* looks like a real HTTP response object."""

    return getattr(response, "headers", None) is not None or callable(
        getattr(response, "getheader", None)
    )


def _response_content_type(response) -> Optional[str]:
    """Return a normalized HTTP media type when response headers are available."""

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


def _smart_http_content_type_matches(response, expected: str) -> Optional[bool]:
    """Compare one real HTTP response media type, or return ``None`` for doubles.

    Older focused tests use minimal response doubles exposing only ``read()``.
    Real ``urllib`` responses expose a header API even if Content-Type itself is
    missing, so ``None`` here can safely mean "no HTTP envelope available to
    validate" rather than "header present but missing".
    """

    if not _response_has_header_api(response):
        return None
    return _response_content_type(response) == expected


def _validate_smart_http_content_type(response, expected: str, *, context: str) -> None:
    """Fail closed on a real smart-HTTP response with an unexpected media type."""

    matches = _smart_http_content_type_matches(response, expected)
    if matches is None or matches:
        return
    content_type = _response_content_type(response)
    rendered = "<missing>" if content_type is None else content_type
    raise ValueError(
        f"Unexpected smart-HTTP {context} Content-Type {rendered!r}; "
        f"expected {expected!r}"
    )


@dataclass(frozen=True)
class ProtocolV2Capabilities:
    """One protocol-v2 capability advertisement."""

    values: Dict[str, Optional[str]]

    def supports(self, name: str) -> bool:
        return name in self.values

    def value(self, name: str) -> Optional[str]:
        return self.values.get(name)

    def feature(self, command: str, name: str) -> bool:
        value = self.values.get(command) or ""
        return name in value.split()


def parse_capability_advertisement(data: bytes) -> Optional[ProtocolV2Capabilities]:
    """Parse a v2 capability advertisement, or return ``None`` for v0."""

    offset = 0
    kind, payload, offset = _read_packet(data, offset)
    if kind != "data" or payload is None:
        return None

    if payload == b"# service=git-upload-pack\n":
        kind, payload, offset = _read_packet(data, offset)
        if kind != "flush":
            return None
        kind, payload, offset = _read_packet(data, offset)

    if kind != "data" or payload is None or payload.rstrip(b"\n") != b"version 2":
        return None

    values: Dict[str, Optional[str]] = {}
    while offset < len(data):
        kind, payload, offset = _read_packet(data, offset)
        if kind == "flush":
            break
        if kind != "data" or payload is None:
            raise ValueError("Unexpected packet in protocol-v2 capability advertisement")
        text = payload.rstrip(b"\n").decode("utf-8")
        if "=" in text:
            key, value = text.split("=", 1)
        else:
            key, value = text, None
        if not key:
            raise ValueError("Empty protocol-v2 capability name")
        values[key] = value

    object_format = values.get("object-format")
    if object_format not in (None, "sha1"):
        raise RuntimeError(
            f"Unsupported remote object format: {object_format}; expected sha1"
        )
    return ProtocolV2Capabilities(values)


def _validate_server_options(
    capabilities: ProtocolV2Capabilities,
    server_options: Sequence[str],
) -> tuple[str, ...]:
    """Validate ordered protocol-v2 server options against the advertisement."""
    options = tuple(server_options)
    if options and not capabilities.supports("server-option"):
        raise RuntimeError("Remote protocol-v2 server does not advertise server-option")
    for option in options:
        if "\n" in option or "\x00" in option:
            raise ValueError("server option contains an invalid NUL or LF character")
    return options


def _command_prefix(
    command: str,
    capabilities: ProtocolV2Capabilities,
    *,
    server_options: Sequence[str] = (),
) -> bytes:
    """Build the capability-list section shared by protocol-v2 commands."""
    body = pkt_line(f"command={command}\n".encode())
    if capabilities.supports("agent"):
        body += pkt_line(b"agent=pygit/0.1\n")
    for option in _validate_server_options(capabilities, server_options):
        body += pkt_line(f"server-option={option}\n".encode())
    return body + b"0001"


def build_ls_refs_request(
    capabilities: ProtocolV2Capabilities,
    *,
    prefixes: Sequence[str] = (),
    server_options: Sequence[str] = (),
) -> bytes:
    """Build one protocol-v2 ``ls-refs`` command request."""

    if not capabilities.supports("ls-refs"):
        raise RuntimeError("Remote protocol-v2 server does not advertise ls-refs")

    body = _command_prefix(
        "ls-refs",
        capabilities,
        server_options=server_options,
    )
    body += pkt_line(b"symrefs\n")
    body += pkt_line(b"peel\n")
    if capabilities.feature("ls-refs", "unborn"):
        body += pkt_line(b"unborn\n")
    for prefix in prefixes:
        if "\n" in prefix or "\x00" in prefix:
            raise ValueError("ls-refs ref-prefix contains an invalid character")
        body += pkt_line(f"ref-prefix {prefix}\n".encode())
    return body + b"0000"


def parse_ls_refs_response(
    data: bytes,
    capabilities: ProtocolV2Capabilities,
) -> Advertisement:
    """Parse ``ls-refs`` output into pygit's existing Advertisement shape."""

    refs: Dict[str, str] = {}
    symrefs: Dict[str, str] = {}
    offset = 0
    while offset < len(data):
        kind, payload, offset = _read_packet(data, offset)
        if kind in {"flush", "response-end"}:
            break
        if kind != "data" or payload is None:
            raise ValueError("Unexpected delimiter in protocol-v2 ls-refs response")
        fields = payload.rstrip(b"\n").decode("utf-8").split(" ")
        if len(fields) < 2:
            raise ValueError("Malformed protocol-v2 ls-refs response line")
        oid, name, *attributes = fields
        if oid != "unborn":
            if len(oid) != 40:
                raise RuntimeError(
                    "Unsupported protocol-v2 object id length; expected SHA-1"
                )
            try:
                int(oid, 16)
            except ValueError as exc:
                raise ValueError("Malformed protocol-v2 object id") from exc
            refs[name] = oid.lower()
        for attribute in attributes:
            if attribute.startswith("symref-target:"):
                symrefs[name] = attribute.split(":", 1)[1]
            elif attribute.startswith("peeled:"):
                peeled = attribute.split(":", 1)[1]
                if len(peeled) != 40:
                    raise RuntimeError(
                        "Unsupported peeled object id length; expected SHA-1"
                    )
                try:
                    int(peeled, 16)
                except ValueError as exc:
                    raise ValueError("Malformed peeled protocol-v2 object id") from exc
                refs[f"{name}^{{}}"] = peeled.lower()

    capability_strings: Set[str] = {
        key if value is None else f"{key}={value}"
        for key, value in capabilities.values.items()
    }
    return Advertisement(refs, capability_strings, symrefs)


class SmartHttpV2QueryClient:
    """Read-only smart-HTTP protocol-v2 capability and ref discovery."""

    def __init__(
        self,
        url: str,
        timeout: int = 30,
        *,
        server_options: Sequence[str] = (),
    ) -> None:
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.server_options = tuple(server_options)

    def discover_capabilities(self) -> Optional[ProtocolV2Capabilities]:
        query = urllib.parse.urlencode({"service": "git-upload-pack"})
        request = urllib.request.Request(
            f"{self.url}/info/refs?{query}",
            headers={
                "Accept": _UPLOAD_PACK_ADVERTISEMENT_MEDIA_TYPE,
                "Git-Protocol": "version=2",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            # gitprotocol-http says a smart discovery response uses the
            # service-specific advertisement media type and clients SHOULD fall
            # back when another type is returned.  Return the existing fallback
            # signal without parsing an untrusted body.  Header-less legacy test
            # doubles continue through the old parser path.
            matches = _smart_http_content_type_matches(
                response,
                _UPLOAD_PACK_ADVERTISEMENT_MEDIA_TYPE,
            )
            if matches is False:
                return None
            return parse_capability_advertisement(response.read())

    def discover_refs(
        self,
        *,
        prefixes: Sequence[str] = (),
    ) -> Optional[Advertisement]:
        """Return v2 refs, or ``None`` when the server falls back to v0."""

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
            return parse_ls_refs_response(response.read(), capabilities)
