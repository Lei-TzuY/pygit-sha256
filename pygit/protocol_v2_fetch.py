"""Git protocol-v2 smart HTTP fetch request/response transport primitives.

Phase 200 introduced the isolated v2 fetch transport. Phase 201 added
``wait-for-done`` ACK-only negotiation. Phase 202 adds the protocol-v2 shallow
request grammar while preserving shallow-info on the returned fetch result.
"""

from __future__ import annotations

import urllib.request
from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Sequence, Tuple

from .protocol_v2 import (
    ProtocolV2Capabilities,
    SmartHttpV2QueryClient,
    _read_packet,
    build_ls_refs_request,
    parse_ls_refs_response,
)
from .remote import Advertisement, FetchResult, PackParser, pkt_line


def _validate_sha1_oid(oid: str, *, field: str) -> str:
    value = oid.lower()
    if len(value) != 40:
        raise ValueError(f"{field} must be a 40-hex SHA-1 object id")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError(f"{field} must be a 40-hex SHA-1 object id") from exc
    return value


def build_fetch_request(
    capabilities: ProtocolV2Capabilities,
    wants: Sequence[str],
    *,
    haves: Iterable[str] = (),
    done: bool = True,
    no_progress: bool = True,
    ofs_delta: bool = True,
    include_tag: bool = False,
    wait_for_done: bool = False,
    shallow: Iterable[str] = (),
    deepen: Optional[int] = None,
    deepen_relative: bool = False,
) -> bytes:
    """Build one protocol-v2 ``fetch`` command request.

    ``deepen`` is absolute by default. ``deepen_relative`` changes it to be
    relative to the client's current shallow boundary, matching Git's
    ``--deepen`` behavior. Any shallow-related argument requires the remote's
    advertised ``fetch=shallow`` feature.
    """

    if not capabilities.supports("fetch"):
        raise RuntimeError("Remote protocol-v2 server does not advertise fetch")

    wanted = sorted({_validate_sha1_oid(oid, field="want") for oid in wants})
    if not wanted:
        raise ValueError("protocol-v2 fetch requires at least one want")
    have_oids = sorted({_validate_sha1_oid(oid, field="have") for oid in haves})
    shallow_oids = sorted(
        {_validate_sha1_oid(oid, field="shallow") for oid in shallow}
    )

    if deepen is not None and deepen <= 0:
        raise ValueError("protocol-v2 deepen must be a positive integer")
    if deepen_relative and deepen is None:
        raise ValueError("deepen-relative requires deepen")
    shallow_requested = bool(shallow_oids) or deepen is not None or deepen_relative
    if shallow_requested and not capabilities.feature("fetch", "shallow"):
        raise RuntimeError("Remote protocol-v2 fetch does not advertise shallow")

    body = pkt_line(b"command=fetch\n")
    if capabilities.supports("agent"):
        body += pkt_line(b"agent=pygit/0.1\n")
    body += b"0001"

    if no_progress:
        body += pkt_line(b"no-progress\n")
    if ofs_delta:
        body += pkt_line(b"ofs-delta\n")
    if include_tag:
        body += pkt_line(b"include-tag\n")
    if wait_for_done:
        if not capabilities.feature("fetch", "wait-for-done"):
            raise RuntimeError(
                "Remote protocol-v2 fetch does not advertise wait-for-done"
            )
        body += pkt_line(b"wait-for-done\n")
    for oid in shallow_oids:
        body += pkt_line(f"shallow {oid}\n".encode())
    if deepen is not None:
        body += pkt_line(f"deepen {deepen}\n".encode())
    if deepen_relative:
        body += pkt_line(b"deepen-relative\n")
    for oid in wanted:
        body += pkt_line(f"want {oid}\n".encode())
    for oid in have_oids:
        body += pkt_line(f"have {oid}\n".encode())
    if done:
        body += pkt_line(b"done\n")
    return body + b"0000"


@dataclass(frozen=True)
class ProtocolV2FetchResponse:
    """Parsed protocol-v2 fetch response sections."""

    acknowledgments: Tuple[str, ...]
    ready: bool
    nak: bool
    shallow: Tuple[str, ...]
    unshallow: Tuple[str, ...]
    wanted_refs: Dict[str, str]
    pack: Optional[bytes]


@dataclass
class V2FetchResult(FetchResult):
    """FetchResult carrying native shallow-info for the importer boundary."""

    shallow: Tuple[str, ...] = ()
    unshallow: Tuple[str, ...] = ()


def _parse_ack_line(
    text: str,
    acknowledgments: list[str],
) -> tuple[bool, bool]:
    if text == "NAK":
        return False, True
    if text == "ready":
        return True, False
    if text.startswith("ACK "):
        oid = _validate_sha1_oid(text[4:], field="ACK")
        acknowledgments.append(oid)
        return False, False
    raise ValueError(f"Malformed protocol-v2 acknowledgment line: {text!r}")


def parse_fetch_response(data: bytes) -> ProtocolV2FetchResponse:
    """Parse the sectioned response to a protocol-v2 ``fetch`` command."""

    acknowledgments: list[str] = []
    ready = False
    nak = False
    shallow: list[str] = []
    unshallow: list[str] = []
    wanted_refs: Dict[str, str] = {}
    pack_chunks: list[bytes] = []
    section: Optional[str] = None
    seen_sections: set[str] = set()
    offset = 0

    while offset < len(data):
        kind, payload, offset = _read_packet(data, offset)
        if kind in {"flush", "response-end"}:
            break
        if kind == "delim":
            section = None
            continue
        if kind != "data" or payload is None:
            raise ValueError("Unexpected packet in protocol-v2 fetch response")

        if section is None:
            try:
                header = payload.rstrip(b"\n").decode("ascii")
            except UnicodeDecodeError as exc:
                raise ValueError("Invalid protocol-v2 fetch section header") from exc
            if header not in {
                "acknowledgments",
                "shallow-info",
                "wanted-refs",
                "packfile-uris",
                "packfile",
            }:
                raise ValueError(f"Unknown protocol-v2 fetch section: {header!r}")
            if header in seen_sections:
                raise ValueError(f"Duplicate protocol-v2 fetch section: {header}")
            if header == "packfile-uris":
                raise RuntimeError(
                    "protocol-v2 packfile-uris response is unsupported; pygit did not request it"
                )
            seen_sections.add(header)
            section = header
            continue

        if section == "packfile":
            channel = payload[0] if payload else 0
            chunk = payload[1:] if payload else b""
            if channel == 1:
                pack_chunks.append(chunk)
            elif channel == 2:
                continue
            elif channel == 3:
                raise RuntimeError(chunk.decode("utf-8", errors="replace").strip())
            else:
                raise ValueError(f"Invalid protocol-v2 pack sideband channel: {channel}")
            continue

        try:
            text = payload.rstrip(b"\n").decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Invalid UTF-8 in protocol-v2 fetch response") from exc

        if section == "acknowledgments":
            line_ready, line_nak = _parse_ack_line(text, acknowledgments)
            ready = ready or line_ready
            nak = nak or line_nak
        elif section == "shallow-info":
            if text.startswith("shallow "):
                shallow.append(_validate_sha1_oid(text[8:], field="shallow"))
            elif text.startswith("unshallow "):
                unshallow.append(_validate_sha1_oid(text[10:], field="unshallow"))
            else:
                raise ValueError(f"Malformed protocol-v2 shallow-info line: {text!r}")
        elif section == "wanted-refs":
            if " " not in text:
                raise ValueError("Malformed protocol-v2 wanted-refs line")
            oid, refname = text.split(" ", 1)
            if not refname:
                raise ValueError("Malformed protocol-v2 wanted-refs line")
            wanted_refs[refname] = _validate_sha1_oid(oid, field="wanted-ref")

    if nak and acknowledgments:
        raise ValueError("protocol-v2 acknowledgments cannot contain both ACK and NAK")

    pack = b"".join(pack_chunks) if pack_chunks else None
    if pack is not None and not pack.startswith(b"PACK"):
        raise ValueError("protocol-v2 packfile section did not contain a packfile")

    return ProtocolV2FetchResponse(
        acknowledgments=tuple(acknowledgments),
        ready=ready,
        nak=nak,
        shallow=tuple(shallow),
        unshallow=tuple(unshallow),
        wanted_refs=wanted_refs,
        pack=pack,
    )


class SmartHttpV2FetchClient(SmartHttpV2QueryClient):
    """Protocol-v2 smart HTTP fetch client with explicit v0 fallback signal."""

    def _discover_refs_with_capabilities(
        self,
        capabilities: ProtocolV2Capabilities,
        *,
        prefixes: Sequence[str] = (),
    ) -> Advertisement:
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

    def _post_fetch(self, body: bytes) -> ProtocolV2FetchResponse:
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
            return parse_fetch_response(response.read())

    @staticmethod
    def _wants(advertisement: Advertisement) -> list[str]:
        return sorted(
            {
                oid
                for name, oid in advertisement.refs.items()
                if name == "HEAD"
                or name.startswith("refs/heads/")
                or (name.startswith("refs/tags/") and not name.endswith("^{}"))
            }
        )

    def fetch(
        self,
        haves: Optional[Iterable[str]] = None,
        advertisement: Optional[Advertisement] = None,
        *,
        shallow: Iterable[str] = (),
        deepen: Optional[int] = None,
        deepen_relative: bool = False,
    ) -> Optional[FetchResult]:
        """Fetch a v2 pack, returning ``None`` when the server answered as v0."""

        capabilities = self.discover_capabilities()
        if capabilities is None:
            return None
        if not capabilities.supports("fetch"):
            raise RuntimeError("Remote protocol-v2 server does not advertise fetch")

        advertisement = advertisement or self._discover_refs_with_capabilities(capabilities)
        wants = self._wants(advertisement)
        if not wants:
            raise RuntimeError("Remote repository does not advertise any refs.")

        body = build_fetch_request(
            capabilities,
            wants,
            haves=haves or (),
            done=True,
            shallow=shallow,
            deepen=deepen,
            deepen_relative=deepen_relative,
        )
        parsed = self._post_fetch(body)

        if parsed.pack is None:
            raise ValueError("protocol-v2 fetch response did not contain a packfile")
        return V2FetchResult(
            advertisement,
            PackParser(parsed.pack).parse(),
            parsed.shallow,
            parsed.unshallow,
        )

    def negotiate(
        self,
        *,
        haves: Iterable[str],
        advertisement: Optional[Advertisement] = None,
    ) -> Optional[Tuple[str, ...]]:
        """Return common native SHA-1 commits, or ``None`` for a v0 server.

        ``wait-for-done`` keeps this as a negotiation-only exchange: because the
        client omits ``done``, a conforming server must not send `ready` or a
        packfile.
        """

        capabilities = self.discover_capabilities()
        if capabilities is None:
            return None
        if not capabilities.supports("fetch"):
            raise RuntimeError("Remote protocol-v2 server does not advertise fetch")

        advertisement = advertisement or self._discover_refs_with_capabilities(capabilities)
        wants = self._wants(advertisement)
        if not wants:
            raise RuntimeError("Remote repository does not advertise any refs.")

        body = build_fetch_request(
            capabilities,
            wants,
            haves=haves,
            done=False,
            wait_for_done=True,
        )
        parsed = self._post_fetch(body)
        if parsed.ready or parsed.pack is not None:
            raise RuntimeError(
                "protocol-v2 negotiate-only unexpectedly advanced to pack transfer"
            )
        return parsed.acknowledgments
