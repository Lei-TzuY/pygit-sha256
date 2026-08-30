"""Protocol-v2 shallow fetch cutoffs using ``deepen-since`` / ``deepen-not``.

This module is deliberately additive: it composes the exact-green Phase309/311
fetch request builder and response parser instead of forking their transport
state machine. Remote object identities remain native SHA-1 at this boundary.
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence

from .protocol_v2 import ProtocolV2Capabilities, _read_packet
from .protocol_v2_fetch import (
    ProtocolV2FetchResponse,
    SmartHttpV2FetchClient,
    V2FetchResult,
    _pkt_line,
    _validate_fetch_response_for_request,
    _validate_sha1_oid,
    build_fetch_request,
)
from .remote import Advertisement, PackParser


def _validate_deepen_since(value: int) -> int:
    """Return one Git timestamp suitable for ``deepen-since``."""

    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("protocol-v2 deepen-since must be a non-negative integer timestamp")
    return value


def _validate_deepen_not_revision(revision: str) -> str:
    """Validate line framing for one remote revision without resolving it locally."""

    if not isinstance(revision, str) or not revision:
        raise ValueError("protocol-v2 deepen-not revision must not be empty")
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in revision):
        raise ValueError("protocol-v2 deepen-not revision must not contain whitespace or controls")
    return revision


def _insert_before_first_want(request: bytes, extra: bytes) -> bytes:
    """Insert shallow cutoff arguments before the first existing ``want`` record."""

    offset = 0
    while offset < len(request):
        start = offset
        kind, payload, offset = _read_packet(request, offset)
        if kind == "data" and payload is not None and payload.startswith(b"want "):
            return request[:start] + extra + request[start:]
    raise RuntimeError("protocol-v2 fetch request unexpectedly contained no want record")


def build_shallow_cutoff_fetch_request(
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
    deepen_since: Optional[int] = None,
    deepen_not: Sequence[str] = (),
    server_options: Sequence[str] = (),
) -> bytes:
    """Build one fetch request cut by time and/or excluded remote revisions.

    Git protocol-v2 allows ``deepen-since`` and repeated ``deepen-not`` records
    together, while both are alternatives to numeric ``deepen``. This focused
    builder does not accept ``deepen`` at all, making the illegal combination
    unrepresentable. Remote revision strings are validated only for pkt-line
    framing; existence and revision resolution remain server responsibilities.
    """

    if not capabilities.supports("fetch"):
        raise RuntimeError("Remote protocol-v2 server does not advertise fetch")
    if not capabilities.feature("fetch", "shallow"):
        raise RuntimeError("Remote protocol-v2 fetch does not advertise shallow")
    if deepen_since is None and not deepen_not:
        raise ValueError("shallow cutoff fetch requires deepen-since and/or deepen-not")

    since = _validate_deepen_since(deepen_since) if deepen_since is not None else None
    excluded = tuple(_validate_deepen_not_revision(revision) for revision in deepen_not)

    base = build_fetch_request(
        capabilities,
        wants,
        haves=haves,
        done=done,
        no_progress=no_progress,
        ofs_delta=ofs_delta,
        include_tag=include_tag,
        wait_for_done=wait_for_done,
        shallow=shallow,
        server_options=server_options,
    )

    extra = b""
    if since is not None:
        extra += _pkt_line(f"deepen-since {since}\n".encode("ascii"))
    for revision in excluded:
        extra += _pkt_line(f"deepen-not {revision}\n".encode("utf-8"))
    return _insert_before_first_want(base, extra)


def validate_shallow_response_for_request(
    response: ProtocolV2FetchResponse,
    *,
    requested_shallow: Iterable[str] = (),
) -> None:
    """Apply request-aware shallow-info rules not knowable by the wire parser."""

    requested = {
        _validate_sha1_oid(oid, field="shallow") for oid in requested_shallow
    }
    unexpected = tuple(oid for oid in response.unshallow if oid not in requested)
    if unexpected:
        rendered = ", ".join(unexpected)
        raise ValueError(
            "protocol-v2 server returned unshallow for an object not declared shallow: "
            + rendered
        )


class SmartHttpV2ShallowCutoffClient(SmartHttpV2FetchClient):
    """Smart HTTP v2 client for time/revision based shallow fetch cutoffs."""

    def fetch_shallow(
        self,
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
        body = build_shallow_cutoff_fetch_request(
            capabilities,
            wants,
            haves=haves or (),
            done=True,
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

        return V2FetchResult(
            advertisement,
            PackParser(parsed.pack).parse(),
            parsed.shallow,
            parsed.unshallow,
        )
