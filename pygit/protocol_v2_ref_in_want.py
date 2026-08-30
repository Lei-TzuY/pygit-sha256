"""Protocol-v2 ``ref-in-want`` fetch support.

This module deliberately layers on top of the strict Phase309 protocol-v2
transport instead of adding a second fetch parser.  ``want-ref`` is useful when
a client wants a named remote ref without first issuing ``ls-refs``; the server
returns the resolved native object ID in the ``wanted-refs`` response section.
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence, Tuple

from .protocol_v2 import ProtocolV2Capabilities, _command_prefix
from .protocol_v2_fetch import (
    ProtocolV2FetchResponse,
    SmartHttpV2FetchClient,
    V2FetchResult,
    _pkt_line,
    _validate_fetch_response_for_request,
    _validate_sha1_oid,
)
from .ref_query import check_ref_format
from .remote import Advertisement, FetchResult, PackParser


def _validated_want_refs(refs: Sequence[str]) -> Tuple[str, ...]:
    """Return safe ordered ``want-ref`` names and reject duplicates.

    Git requires ``want-ref`` to name a server ref and treats duplicate
    ``want-ref`` arguments as a protocol error.  We preserve caller order and
    use pygit's existing check-ref-format implementation only as an injection /
    structural safety boundary.  One-level names remain syntactically allowed
    because native Git accepts e.g. ``HEAD``; whether a safe name actually
    exists is still the remote server's authority.
    """

    checked: list[str] = []
    seen: set[str] = set()
    for refname in refs:
        candidate = check_ref_format(refname, allow_onelevel=True)
        if candidate in seen:
            raise ValueError(f"duplicate protocol-v2 want-ref {candidate}")
        seen.add(candidate)
        checked.append(candidate)
    if not checked:
        raise ValueError("protocol-v2 ref-in-want requires at least one ref")
    return tuple(checked)


def build_ref_in_want_request(
    capabilities: ProtocolV2Capabilities,
    refs: Sequence[str],
    *,
    haves: Iterable[str] = (),
    no_progress: bool = True,
    ofs_delta: bool = True,
    include_tag: bool = False,
    server_options: Sequence[str] = (),
) -> bytes:
    """Build one terminating protocol-v2 ``fetch`` using ``want-ref`` lines.

    The request is intentionally one-shot: it always sends ``done`` and expects
    a packfile plus an exact ``wanted-refs`` mapping.  Multi-round negotiation
    remains the responsibility of the ordinary OID-based fetch path.
    """

    if not capabilities.supports("fetch"):
        raise RuntimeError("Remote protocol-v2 server does not advertise fetch")
    if not capabilities.feature("fetch", "ref-in-want"):
        raise RuntimeError(
            "Remote protocol-v2 fetch does not advertise ref-in-want"
        )

    requested_refs = _validated_want_refs(refs)
    have_oids = sorted({_validate_sha1_oid(oid, field="have") for oid in haves})

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
    for refname in requested_refs:
        body += _pkt_line(f"want-ref {refname}\n".encode("utf-8"))
    for oid in have_oids:
        body += _pkt_line(f"have {oid}\n".encode())
    body += _pkt_line(b"done\n")
    return body + b"0000"


def validate_ref_in_want_response(
    response: ProtocolV2FetchResponse,
    requested_refs: Sequence[str],
) -> None:
    """Validate a successful one-shot ``ref-in-want`` response.

    Git specifies that a wanted-refs section accompanies the packfile and lists
    every requested ref while never listing an unrequested ref.  Enforce that
    exact set at the request-aware boundary after the shared fetch state machine
    has validated framing, ordering, object IDs, and the terminating packfile.
    """

    requested = set(_validated_want_refs(requested_refs))
    _validate_fetch_response_for_request(response, done=True)

    returned = set(response.wanted_refs)
    unexpected = sorted(returned - requested)
    if unexpected:
        raise ValueError(
            "protocol-v2 wanted-refs response contained unrequested refs: "
            + ", ".join(unexpected)
        )

    missing = sorted(requested - returned)
    if missing:
        raise ValueError(
            "protocol-v2 wanted-refs response omitted requested refs: "
            + ", ".join(missing)
        )


class SmartHttpV2RefInWantClient(SmartHttpV2FetchClient):
    """Smart-HTTP client for direct named-ref fetches without ``ls-refs``."""

    def fetch_refs(
        self,
        refs: Sequence[str],
        haves: Optional[Iterable[str]] = None,
    ) -> Optional[FetchResult]:
        """Fetch named remote refs directly using protocol-v2 ``want-ref``.

        Return ``None`` only when capability discovery indicates the server did
        not enter protocol v2, matching the existing v2 client fallback signal.
        """

        capabilities = self.discover_capabilities()
        if capabilities is None:
            return None

        requested_refs = _validated_want_refs(refs)
        body = build_ref_in_want_request(
            capabilities,
            requested_refs,
            haves=haves or (),
            server_options=self.server_options,
        )
        parsed = self._post_fetch(body)
        validate_ref_in_want_response(parsed, requested_refs)
        assert parsed.pack is not None

        advertisement = Advertisement(dict(parsed.wanted_refs), set(), {})
        return V2FetchResult(
            advertisement,
            PackParser(parsed.pack).parse(),
            parsed.shallow,
            parsed.unshallow,
        )
