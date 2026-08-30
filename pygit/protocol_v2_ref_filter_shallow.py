"""Direct protocol-v2 named-ref fetch with object filtering and shallow cutoffs.

Phase316 composes the exact-green ref-in-want, filtered-fetch, and shallow-cutoff
primitives without adding another response parser. Remote identities stay native
SHA-1 until received object content crosses the existing importer boundary.
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence

from .protocol_v2 import ProtocolV2Capabilities, _command_prefix
from .protocol_v2_fetch import V2FetchResult, _pkt_line, _validate_sha1_oid
from .protocol_v2_filter_fetch import normalize_filter_spec
from .protocol_v2_ref_in_want import (
    SmartHttpV2RefInWantClient,
    _validated_want_refs,
    validate_ref_in_want_response,
)
from .protocol_v2_shallow_cutoff import (
    _validate_deepen_not_revision,
    _validate_deepen_since,
    validate_shallow_response_for_request,
)
from .remote import Advertisement, FetchResult, PackParser


def build_ref_filtered_shallow_request(
    capabilities: ProtocolV2Capabilities,
    refs: Sequence[str],
    filter_spec: str,
    *,
    haves: Iterable[str] = (),
    no_progress: bool = True,
    ofs_delta: bool = True,
    include_tag: bool = False,
    shallow: Iterable[str] = (),
    deepen_since: Optional[int] = None,
    deepen_not: Sequence[str] = (),
    server_options: Sequence[str] = (),
) -> bytes:
    """Build one terminating ``want-ref`` fetch with filter and shallow cutoffs.

    The request intentionally requires all three advertised features.  It keeps
    ref validation delegated to Phase311, filter-spec normalization to Phase312,
    and cutoff validation to Phase313.  Remote revision expressions supplied to
    ``deepen-not`` are never resolved against local repository state.
    """

    if not capabilities.supports("fetch"):
        raise RuntimeError("Remote protocol-v2 server does not advertise fetch")
    if not capabilities.feature("fetch", "ref-in-want"):
        raise RuntimeError("Remote protocol-v2 fetch does not advertise ref-in-want")
    if not capabilities.feature("fetch", "shallow"):
        raise RuntimeError("Remote protocol-v2 fetch does not advertise shallow")
    if not capabilities.feature("fetch", "filter"):
        raise RuntimeError("Remote protocol-v2 fetch does not advertise filter")
    if deepen_since is None and not deepen_not:
        raise ValueError("direct filtered shallow fetch requires deepen-since and/or deepen-not")

    requested_refs = _validated_want_refs(refs)
    have_oids = sorted({_validate_sha1_oid(oid, field="have") for oid in haves})
    shallow_oids = sorted(
        {_validate_sha1_oid(oid, field="shallow") for oid in shallow}
    )
    since = _validate_deepen_since(deepen_since) if deepen_since is not None else None
    excluded = tuple(_validate_deepen_not_revision(revision) for revision in deepen_not)
    normalized_filter = normalize_filter_spec(filter_spec)

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

    for oid in shallow_oids:
        body += _pkt_line(f"shallow {oid}\n".encode("ascii"))
    if since is not None:
        body += _pkt_line(f"deepen-since {since}\n".encode("ascii"))
    for revision in excluded:
        body += _pkt_line(f"deepen-not {revision}\n".encode("utf-8"))
    for refname in requested_refs:
        body += _pkt_line(f"want-ref {refname}\n".encode("utf-8"))
    for oid in have_oids:
        body += _pkt_line(f"have {oid}\n".encode("ascii"))
    body += _pkt_line(f"filter {normalized_filter}\n".encode("utf-8"))
    body += _pkt_line(b"done\n")
    return body + b"0000"


class SmartHttpV2RefFilteredShallowClient(SmartHttpV2RefInWantClient):
    """Direct named-ref partial/shallow fetch without an ``ls-refs`` round trip."""

    def fetch_refs_filtered_shallow(
        self,
        refs: Sequence[str],
        filter_spec: str,
        *,
        deepen_since: Optional[int] = None,
        deepen_not: Sequence[str] = (),
        haves: Optional[Iterable[str]] = None,
        shallow: Iterable[str] = (),
    ) -> Optional[FetchResult]:
        capabilities = self.discover_capabilities()
        if capabilities is None:
            return None

        requested_refs = _validated_want_refs(refs)
        shallow_oids = tuple(shallow)
        body = build_ref_filtered_shallow_request(
            capabilities,
            requested_refs,
            filter_spec,
            haves=haves or (),
            shallow=shallow_oids,
            deepen_since=deepen_since,
            deepen_not=deepen_not,
            server_options=self.server_options,
        )
        parsed = self._post_fetch(body)
        validate_ref_in_want_response(parsed, requested_refs)
        validate_shallow_response_for_request(
            parsed,
            requested_shallow=shallow_oids,
        )
        assert parsed.pack is not None

        advertisement = Advertisement(dict(parsed.wanted_refs), set(), {})
        return V2FetchResult(
            advertisement,
            PackParser(parsed.pack).parse(),
            parsed.shallow,
            parsed.unshallow,
        )
