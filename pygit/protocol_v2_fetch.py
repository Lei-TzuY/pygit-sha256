"""Git protocol-v2 smart HTTP fetch request/response transport primitives.

Phase 200 introduced the isolated v2 fetch transport. Phase 201 added
``wait-for-done`` ACK-only negotiation. Phase 202 added protocol-v2 shallow
request grammar, and Phase207 reconciles ordered ``server-option`` forwarding
with that shallow transport without changing repository-visible SHA-256 identity.
"""

from __future__ import annotations

import urllib.request
from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Sequence, Tuple

from .protocol_v2 import (
    ProtocolV2Capabilities,
    SmartHttpV2QueryClient,
    _UPLOAD_PACK_REQUEST_MEDIA_TYPE,
    _UPLOAD_PACK_RESULT_MEDIA_TYPE,
    _command_prefix,
    _read_packet,
    _validate_smart_http_content_type,
    build_ls_refs_request,
    parse_ls_refs_response,
)
from .remote import Advertisement, FetchResult, PackParser


_FETCH_SECTION_ORDER = {
    "acknowledgments": 0,
    "shallow-info": 1,
    "wanted-refs": 2,
    "packfile-uris": 3,
    "packfile": 4,
}


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
    server_options: Sequence[str] = (),
) -> bytes:
    """Build one protocol-v2 ``fetch`` command request.

    ``deepen`` is absolute by default. ``deepen_relative`` changes it to be
    relative to the client's current shallow boundary. Ordered server options
    are emitted in the command capability-list before the delimiter.
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

    if wait_for_done:
        if done:
            raise ValueError("wait-for-done cannot be combined with done")
        if not capabilities.feature("fetch", "wait-for-done"):
            raise RuntimeError(
                "Remote protocol-v2 fetch does not advertise wait-for-done"
            )

    body = _command_prefix(
        "fetch",
        capabilities,
        server_options=server_options,
    )

    if no_progress:
        body += _pkt_line(b"no-progress\n")
    if ofs_delta:
        body += _pkt_line(b"ofs-delta\n")
    if include_tag:
        body += _pkt_line(b"include-tag\n")
    if wait_for_done:
        body += _pkt_line(b"wait-for-done\n")
    for oid in shallow_oids:
        body += _pkt_line(f"shallow {oid}\n".encode())
    if deepen is not None:
        body += _pkt_line(f"deepen {deepen}\n".encode())
    if deepen_relative:
        body += _pkt_line(b"deepen-relative\n")
    for oid in wanted:
        body += _pkt_line(f"want {oid}\n".encode())
    for oid in have_oids:
        body += _pkt_line(f"have {oid}\n".encode())
    if done:
        body += _pkt_line(b"done\n")
    return body + b"0000"


def _pkt_line(payload: bytes) -> bytes:
    # Local alias keeps the request builder independent from the legacy client.
    return f"{len(payload) + 4:04x}".encode() + payload


def _decode_fetch_text(payload: bytes, *, context: str, ascii_only: bool = False) -> str:
    """Decode one textual fetch pkt-line without silently normalizing records.

    Git's pkt-line rules require receivers to tolerate a missing terminal LF, so
    accept either no LF or exactly one terminal LF. Embedded/repeated LF, CR,
    and NUL bytes remain structural errors rather than being hidden by rstrip().
    """

    if payload.endswith(b"\n"):
        payload = payload[:-1]
    if b"\n" in payload:
        raise ValueError(f"Unexpected LF inside {context}")
    if b"\r" in payload:
        raise ValueError(f"Unexpected CR inside {context}")
    if b"\x00" in payload:
        raise ValueError(f"Unexpected NUL inside {context}")
    try:
        return payload.decode("ascii" if ascii_only else "utf-8")
    except UnicodeDecodeError as exc:
        encoding = "ASCII" if ascii_only else "UTF-8"
        raise ValueError(f"Invalid {encoding} in {context}") from exc


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

    @property
    def has_acknowledgments(self) -> bool:
        """Return whether an acknowledgments section was observably present."""

        return bool(self.acknowledgments or self.ready or self.nak)


@dataclass
class V2FetchResult(FetchResult):
    """FetchResult carrying native shallow-info for the importer boundary."""

    shallow: Tuple[str, ...] = ()
    unshallow: Tuple[str, ...] = ()


def _validate_fetch_response_for_request(
    response: ProtocolV2FetchResponse,
    *,
    done: bool = False,
    wait_for_done: bool = False,
) -> None:
    """Validate response semantics that depend on the request negotiation mode."""

    if done:
        if response.has_acknowledgments:
            raise ValueError(
                "protocol-v2 done fetch response must omit acknowledgments"
            )
        if response.pack is None:
            raise ValueError("protocol-v2 done fetch response did not contain a packfile")

    if wait_for_done:
        if response.ready:
            raise ValueError(
                "protocol-v2 wait-for-done response must not contain ready"
            )
        if response.pack is not None:
            raise ValueError(
                "protocol-v2 wait-for-done response must not contain a packfile before done"
            )
        if not response.has_acknowledgments:
            raise ValueError(
                "protocol-v2 wait-for-done response must contain acknowledgments"
            )


def parse_fetch_response(data: bytes) -> ProtocolV2FetchResponse:
    """Parse one complete sectioned response to a protocol-v2 ``fetch`` command.

    Git's current grammar permits either one acknowledgments-only response or a
    strictly ordered section stream ending in ``packfile``. Delimiter packets
    separate sections; they cannot appear before the first section, repeat, or
    follow the final packfile section. The final response terminator is one
    flush-pkt, with no trailing bytes.
    """

    acknowledgments: list[str] = []
    acknowledgment_set: set[str] = set()
    ready = False
    nak = False
    shallow: list[str] = []
    unshallow: list[str] = []
    shallow_seen: set[str] = set()
    unshallow_seen: set[str] = set()
    wanted_refs: Dict[str, str] = {}
    pack_chunks: list[bytes] = []

    section: Optional[str] = None
    seen_sections: set[str] = set()
    last_section_rank = -1
    saw_flush = False
    offset = 0

    def validate_section_end(name: str) -> None:
        if name == "acknowledgments" and not (
            acknowledgments or ready or nak
        ):
            raise ValueError(
                "protocol-v2 acknowledgments section contained no result"
            )

    while offset < len(data):
        kind, payload, offset = _read_packet(data, offset)

        if kind == "flush":
            if section is None:
                if seen_sections:
                    raise ValueError(
                        "protocol-v2 fetch response ended immediately after delimiter"
                    )
                raise ValueError("protocol-v2 fetch response contained no section")
            validate_section_end(section)
            saw_flush = True
            if offset != len(data):
                raise ValueError("Trailing data after protocol-v2 fetch flush packet")
            break

        if kind == "response-end":
            raise ValueError(
                "Unexpected response-end-pkt in protocol-v2 fetch response"
            )

        if kind == "delim":
            if section is None:
                raise ValueError(
                    "Unexpected delimiter before protocol-v2 fetch section"
                )
            if section == "packfile":
                raise ValueError(
                    "Unexpected delimiter after protocol-v2 packfile section"
                )
            validate_section_end(section)
            section = None
            continue

        if kind != "data" or payload is None:
            raise ValueError("Unexpected packet in protocol-v2 fetch response")

        if section is None:
            header = _decode_fetch_text(
                payload,
                context="protocol-v2 fetch section header",
                ascii_only=True,
            )
            if header not in _FETCH_SECTION_ORDER:
                raise ValueError(f"Unknown protocol-v2 fetch section: {header!r}")
            if header in seen_sections:
                raise ValueError(f"Duplicate protocol-v2 fetch section: {header}")

            rank = _FETCH_SECTION_ORDER[header]
            if rank <= last_section_rank:
                raise ValueError(
                    f"Out-of-order protocol-v2 fetch section: {header}"
                )
            if header == "packfile-uris":
                raise RuntimeError(
                    "protocol-v2 packfile-uris response is unsupported; pygit did not request it"
                )

            seen_sections.add(header)
            last_section_rank = rank
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

        text = _decode_fetch_text(
            payload,
            context=f"protocol-v2 {section} record",
            ascii_only=section != "wanted-refs",
        )

        if section == "acknowledgments":
            if text == "NAK":
                if nak:
                    raise ValueError("Duplicate NAK in protocol-v2 acknowledgments")
                if acknowledgments or ready:
                    raise ValueError(
                        "protocol-v2 acknowledgments cannot mix NAK with ACK or ready"
                    )
                nak = True
            elif text == "ready":
                if ready:
                    raise ValueError("Duplicate ready in protocol-v2 acknowledgments")
                if nak:
                    raise ValueError(
                        "protocol-v2 acknowledgments cannot mix NAK with ready"
                    )
                ready = True
            elif text.startswith("ACK "):
                if nak:
                    raise ValueError(
                        "protocol-v2 acknowledgments cannot contain both ACK and NAK"
                    )
                if ready:
                    raise ValueError(
                        "protocol-v2 ACK cannot appear after ready"
                    )
                oid = _validate_sha1_oid(text[4:], field="ACK")
                if oid in acknowledgment_set:
                    raise ValueError(f"Duplicate protocol-v2 ACK for {oid}")
                acknowledgment_set.add(oid)
                acknowledgments.append(oid)
            else:
                raise ValueError(
                    f"Malformed protocol-v2 acknowledgment line: {text!r}"
                )

        elif section == "shallow-info":
            if text.startswith("shallow "):
                oid = _validate_sha1_oid(text[8:], field="shallow")
                if oid in shallow_seen:
                    raise ValueError(f"Duplicate protocol-v2 shallow result for {oid}")
                if oid in unshallow_seen:
                    raise ValueError(
                        f"Conflicting shallow/unshallow result for {oid}"
                    )
                shallow_seen.add(oid)
                shallow.append(oid)
            elif text.startswith("unshallow "):
                oid = _validate_sha1_oid(text[10:], field="unshallow")
                if oid in unshallow_seen:
                    raise ValueError(f"Duplicate protocol-v2 unshallow result for {oid}")
                if oid in shallow_seen:
                    raise ValueError(
                        f"Conflicting shallow/unshallow result for {oid}"
                    )
                unshallow_seen.add(oid)
                unshallow.append(oid)
            else:
                raise ValueError(f"Malformed protocol-v2 shallow-info line: {text!r}")

        elif section == "wanted-refs":
            oid, separator, refname = text.partition(" ")
            if not separator or not refname or " " in refname:
                raise ValueError("Malformed protocol-v2 wanted-refs line")
            if refname in wanted_refs:
                raise ValueError(
                    f"Duplicate protocol-v2 wanted-ref result for {refname}"
                )
            wanted_refs[refname] = _validate_sha1_oid(oid, field="wanted-ref")

    if not saw_flush:
        raise ValueError("protocol-v2 fetch response did not end with flush packet")

    has_packfile_section = "packfile" in seen_sections
    non_ack_sections = seen_sections - {"acknowledgments"}
    if non_ack_sections and not has_packfile_section:
        raise ValueError(
            "protocol-v2 fetch response with non-acknowledgment sections must end in packfile"
        )
    if ready and not has_packfile_section:
        raise ValueError(
            "protocol-v2 ready acknowledgment requires a packfile in the same response"
        )
    if "acknowledgments" in seen_sections and has_packfile_section and not ready:
        raise ValueError(
            "protocol-v2 acknowledgments preceding packfile must contain ready"
        )

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

    def _post_fetch(self, body: bytes) -> ProtocolV2FetchResponse:
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
            server_options=self.server_options,
        )
        parsed = self._post_fetch(body)
        _validate_fetch_response_for_request(parsed, done=True)

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
        """Return common native SHA-1 commits, or ``None`` for a v0 server."""

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
            server_options=self.server_options,
        )
        parsed = self._post_fetch(body)
        if parsed.ready or parsed.pack is not None:
            raise RuntimeError(
                "protocol-v2 negotiate-only unexpectedly advanced to pack transfer"
            )
        _validate_fetch_response_for_request(parsed, wait_for_done=True)
        return parsed.acknowledgments
