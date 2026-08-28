"""Read-only Git protocol-v2 smart HTTP discovery and ``ls-refs``.

This module deliberately stops at reference discovery. Object transfer remains
on pygit's established protocol-v0 ``SmartHttpClient`` until the protocol-v2
``fetch`` command and its sectioned response parser are implemented.
"""

from __future__ import annotations

import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Set

from .remote import Advertisement, pkt_line


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

    # Be tolerant of HTTP helpers that retain a v0 service preamble before a
    # fallback response. A conforming v2 response starts directly at version 2.
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


def build_ls_refs_request(
    capabilities: ProtocolV2Capabilities,
    *,
    prefixes: Sequence[str] = (),
) -> bytes:
    """Build one protocol-v2 ``ls-refs`` command request."""

    if not capabilities.supports("ls-refs"):
        raise RuntimeError("Remote protocol-v2 server does not advertise ls-refs")

    body = pkt_line(b"command=ls-refs\n")
    if capabilities.supports("agent"):
        body += pkt_line(b"agent=pygit/0.1\n")
    body += b"0001"
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

    def __init__(self, url: str, timeout: int = 30) -> None:
        self.url = url.rstrip("/")
        self.timeout = timeout

    def discover_capabilities(self) -> Optional[ProtocolV2Capabilities]:
        query = urllib.parse.urlencode({"service": "git-upload-pack"})
        request = urllib.request.Request(
            f"{self.url}/info/refs?{query}",
            headers={
                "Accept": "application/x-git-upload-pack-advertisement",
                "Git-Protocol": "version=2",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
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
        body = build_ls_refs_request(capabilities, prefixes=prefixes)
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
            return parse_ls_refs_response(response.read(), capabilities)
