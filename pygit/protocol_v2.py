"""Git protocol-v2 smart HTTP discovery, ``ls-refs``, and ``fetch``.

Protocol v2 keeps reference discovery separate from object transfer.  This
module implements both commands while keeping native SHA-1 object names at the
smart-HTTP boundary; pygit's repository-visible identity remains SHA-256.
"""

from __future__ import annotations

import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Sequence, Set, Tuple

from .remote import Advertisement, FetchResult, PackParser, pkt_line


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


def _validate_oid(oid: str, *, context: str = "object id") -> str:
    if len(oid) != 40:
        raise RuntimeError(f"Unsupported protocol-v2 {context} length; expected SHA-1")
    try:
        int(oid, 16)
    except ValueError as exc:
        raise ValueError(f"Malformed protocol-v2 {context}") from exc
    return oid.lower()


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


@dataclass(frozen=True)
class ProtocolV2FetchResponse:
    """Parsed sectioned response from one protocol-v2 ``fetch`` command."""

    acknowledgments: Tuple[str, ...]
    ready: bool
    shallow: Tuple[str, ...]
    unshallow: Tuple[str, ...]
    wanted_refs: Tuple[Tuple[str, str], ...]
    pack: Optional[bytes]


class ProtocolV2Unavailable(RuntimeError):
    """Raised when the remote does not negotiate protocol version 2."""


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
            refs[name] = _validate_oid(oid)
        for attribute in attributes:
            if attribute.startswith("symref-target:"):
                symrefs[name] = attribute.split(":", 1)[1]
            elif attribute.startswith("peeled:"):
                peeled = attribute.split(":", 1)[1]
                refs[f"{name}^{{}}"] = _validate_oid(peeled, context="peeled object id")

    capability_strings: Set[str] = {
        key if value is None else f"{key}={value}"
        for key, value in capabilities.values.items()
    }
    return Advertisement(refs, capability_strings, symrefs)


def build_fetch_request(
    capabilities: ProtocolV2Capabilities,
    *,
    wants: Iterable[str],
    haves: Iterable[str] = (),
    done: bool = True,
    wait_for_done: bool = False,
    include_tag: bool = False,
    no_progress: bool = True,
    ofs_delta: bool = True,
) -> bytes:
    """Build one stateless protocol-v2 ``fetch`` command request."""

    if not capabilities.supports("fetch"):
        raise RuntimeError("Remote protocol-v2 server does not advertise fetch")
    want_list = sorted({_validate_oid(oid, context="want object id") for oid in wants})
    if not want_list:
        raise RuntimeError("protocol-v2 fetch requires at least one want")
    have_list = sorted({_validate_oid(oid, context="have object id") for oid in haves})

    body = pkt_line(b"command=fetch\n")
    if capabilities.supports("agent"):
        body += pkt_line(b"agent=pygit/0.1\n")
    body += b"0001"
    if no_progress:
        body += pkt_line(b"no-progress\n")
    if include_tag:
        body += pkt_line(b"include-tag\n")
    if ofs_delta:
        body += pkt_line(b"ofs-delta\n")
    if wait_for_done:
        if not capabilities.feature("fetch", "wait-for-done"):
            raise RuntimeError(
                "Remote protocol-v2 fetch does not advertise wait-for-done"
            )
        body += pkt_line(b"wait-for-done\n")
    for oid in want_list:
        body += pkt_line(f"want {oid}\n".encode())
    for oid in have_list:
        body += pkt_line(f"have {oid}\n".encode())
    if done:
        body += pkt_line(b"done\n")
    return body + b"0000"


def parse_fetch_response(data: bytes) -> ProtocolV2FetchResponse:
    """Parse the sectioned protocol-v2 ``fetch`` response.

    pygit deliberately does not request ``sideband-all`` yet, so only the
    packfile section is multiplexed. Channel 1 carries pack bytes, channel 2 is
    progress, and channel 3 is a fatal transport error.
    """

    acknowledgments = []
    ready = False
    shallow = []
    unshallow = []
    wanted_refs: Dict[str, str] = {}
    pack_chunks = []
    section: Optional[str] = None
    offset = 0

    while offset < len(data):
        kind, payload, offset = _read_packet(data, offset)
        if kind in {"flush", "response-end"}:
            break
        if kind == "delim":
            section = None
            continue
        if kind != "data" or payload is None:
            raise ValueError("Unexpected control packet in protocol-v2 fetch response")

        if section is None:
            header = payload.rstrip(b"\n").decode("utf-8", errors="replace")
            if header not in {
                "acknowledgments",
                "shallow-info",
                "wanted-refs",
                "packfile",
            }:
                raise ValueError(f"Unknown protocol-v2 fetch response section: {header!r}")
            section = header
            continue

        if section == "acknowledgments":
            text = payload.rstrip(b"\n").decode("ascii", errors="strict")
            if text == "NAK":
                continue
            if text == "ready":
                ready = True
                continue
            if text.startswith("ACK "):
                acknowledgments.append(
                    _validate_oid(text[4:], context="acknowledgment object id")
                )
                continue
            raise ValueError(f"Malformed protocol-v2 acknowledgment: {text!r}")

        if section == "shallow-info":
            text = payload.rstrip(b"\n").decode("ascii", errors="strict")
            if text.startswith("shallow "):
                shallow.append(_validate_oid(text[8:], context="shallow object id"))
                continue
            if text.startswith("unshallow "):
                unshallow.append(_validate_oid(text[10:], context="unshallow object id"))
                continue
            raise ValueError(f"Malformed protocol-v2 shallow-info line: {text!r}")

        if section == "wanted-refs":
            text = payload.rstrip(b"\n").decode("utf-8")
            try:
                oid, refname = text.split(" ", 1)
            except ValueError as exc:
                raise ValueError("Malformed protocol-v2 wanted-ref line") from exc
            wanted_refs[refname] = _validate_oid(oid, context="wanted-ref object id")
            continue

        if section == "packfile":
            if not payload:
                raise ValueError("Empty protocol-v2 packfile sideband packet")
            channel, chunk = payload[0], payload[1:]
            if channel == 1:
                pack_chunks.append(chunk)
            elif channel == 2:
                continue
            elif channel == 3:
                raise RuntimeError(chunk.decode("utf-8", errors="replace").strip())
            else:
                raise ValueError(f"Unknown protocol-v2 packfile sideband channel: {channel}")

    pack = b"".join(pack_chunks) if pack_chunks else None
    if pack is not None and not pack.startswith(b"PACK"):
        raise ValueError("protocol-v2 fetch response did not contain a valid packfile")
    return ProtocolV2FetchResponse(
        acknowledgments=tuple(acknowledgments),
        ready=ready,
        shallow=tuple(shallow),
        unshallow=tuple(unshallow),
        wanted_refs=tuple(sorted(wanted_refs.items())),
        pack=pack,
    )


class SmartHttpV2QueryClient:
    """Smart-HTTP protocol-v2 capability and reference discovery."""

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

    def _post(self, body: bytes) -> bytes:
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
            return response.read()

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
        return parse_ls_refs_response(self._post(body), capabilities)


class SmartHttpV2FetchClient(SmartHttpV2QueryClient):
    """Stateless smart-HTTP implementation of protocol-v2 ``fetch``."""

    def __init__(self, url: str, timeout: int = 30) -> None:
        super().__init__(url, timeout)
        self._capabilities: Optional[ProtocolV2Capabilities] = None

    def capabilities(self) -> ProtocolV2Capabilities:
        if self._capabilities is None:
            discovered = self.discover_capabilities()
            if discovered is None:
                raise ProtocolV2Unavailable("Remote did not negotiate protocol version 2")
            self._capabilities = discovered
        return self._capabilities

    def discover(self, *, prefixes: Sequence[str] = ()) -> Advertisement:
        capabilities = self.capabilities()
        body = build_ls_refs_request(capabilities, prefixes=prefixes)
        return parse_ls_refs_response(self._post(body), capabilities)

    @staticmethod
    def wants_from_advertisement(advertisement: Advertisement) -> Tuple[str, ...]:
        return tuple(
            sorted(
                {
                    oid
                    for name, oid in advertisement.refs.items()
                    if name == "HEAD"
                    or name.startswith("refs/heads/")
                    or (name.startswith("refs/tags/") and not name.endswith("^{}"))
                }
            )
        )

    def fetch(
        self,
        haves: Optional[Iterable[str]] = None,
        advertisement: Optional[Advertisement] = None,
    ) -> FetchResult:
        advertisement = advertisement or self.discover()
        wants = self.wants_from_advertisement(advertisement)
        body = build_fetch_request(
            self.capabilities(),
            wants=wants,
            haves=haves or (),
            done=True,
        )
        parsed = parse_fetch_response(self._post(body))
        if parsed.pack is None:
            raise ValueError("protocol-v2 fetch response omitted the packfile section")
        return FetchResult(advertisement, PackParser(parsed.pack).parse())

    def negotiate(
        self,
        *,
        haves: Iterable[str],
        advertisement: Optional[Advertisement] = None,
    ) -> Tuple[str, ...]:
        """Return common native object IDs without requesting a packfile."""

        advertisement = advertisement or self.discover()
        wants = self.wants_from_advertisement(advertisement)
        body = build_fetch_request(
            self.capabilities(),
            wants=wants,
            haves=haves,
            done=False,
            wait_for_done=True,
        )
        parsed = parse_fetch_response(self._post(body))
        if parsed.pack is not None:
            raise RuntimeError("protocol-v2 negotiate-only unexpectedly received a packfile")
        return parsed.acknowledgments
