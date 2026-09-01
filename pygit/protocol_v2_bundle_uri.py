"""Protocol-v2 ``bundle-uri`` discovery without bundle materialization.

This module deliberately stops at the transport/list boundary.  It discovers a
server-advertised bundle list and normalizes the currently documented metadata,
but it never follows any advertised URI and never writes repository state.
"""

from __future__ import annotations

import re
import urllib.request
from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple

from .protocol_v2 import (
    ProtocolV2Capabilities,
    SmartHttpV2QueryClient,
    _UPLOAD_PACK_REQUEST_MEDIA_TYPE,
    _UPLOAD_PACK_RESULT_MEDIA_TYPE,
    _command_prefix,
    _payload_without_optional_lf,
    _read_packet,
    _validate_smart_http_content_type,
)


_BUNDLE_ID_RE = re.compile(r"^[A-Za-z0-9-]+$")
_MAX_CREATION_TOKEN = (1 << 64) - 1


@dataclass(frozen=True)
class BundleUriEntry:
    """One bundle descriptor advertised by a protocol-v2 bundle list."""

    bundle_id: str
    uri: str
    filter_spec: Optional[str] = None
    creation_token: Optional[int] = None
    location: Optional[str] = None


@dataclass(frozen=True)
class BundleUriList:
    """A usable version-1 bundle list.

    Git currently defaults an in-memory protocol bundle list to version 1 and
    ``mode=all`` before consuming server key/value records.  Preserve that
    observable compatibility while treating explicit unsupported values as an
    unusable optional acceleration hint.
    """

    version: int
    mode: str
    heuristic: Optional[str]
    bundles: Tuple[BundleUriEntry, ...]


def build_bundle_uri_request(
    capabilities: ProtocolV2Capabilities,
    *,
    server_options: Sequence[str] = (),
) -> bytes:
    """Build the argument-free protocol-v2 ``bundle-uri`` command request."""

    if not capabilities.supports("bundle-uri"):
        raise RuntimeError("Remote protocol-v2 server does not advertise bundle-uri")
    return _command_prefix(
        "bundle-uri",
        capabilities,
        server_options=server_options,
    ) + b"0000"


def _parse_bundle_uri_pairs(data: bytes) -> Tuple[Tuple[str, str], ...]:
    """Parse a complete bundle-uri response into well-formed key/value pairs.

    Pkt-line framing is a transport invariant and therefore fails closed.
    Individual textual records are optional acceleration metadata; the Git
    protocol explicitly permits malformed non-``key=value`` records to be
    discarded, so textual record errors do not poison the normal fetch path.
    """

    offset = 0
    saw_flush = False
    pairs = []

    while offset < len(data):
        kind, payload, offset = _read_packet(data, offset)
        if kind == "flush":
            saw_flush = True
            if offset != len(data):
                raise ValueError("Trailing data after protocol-v2 bundle-uri flush packet")
            break
        if kind in {"delim", "response-end"}:
            raise ValueError("Unexpected non-flush terminator in protocol-v2 bundle-uri response")
        if kind != "data" or payload is None:
            raise ValueError("Unexpected packet in protocol-v2 bundle-uri response")

        try:
            raw = _payload_without_optional_lf(
                payload,
                context="protocol-v2 bundle-uri record",
            )
            text = raw.decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            continue

        if "\x00" in text:
            continue
        key, separator, value = text.partition("=")
        if not separator or not key or not value:
            continue
        pairs.append((key, value))

    if not saw_flush:
        raise ValueError("protocol-v2 bundle-uri response did not end with flush packet")

    return tuple(pairs)


def _creation_token(value: str) -> Optional[int]:
    """Return one documented nonnegative uint64 creation token, else ignore it."""

    if not value.isdigit():
        return None
    token = int(value, 10)
    if token > _MAX_CREATION_TOKEN:
        return None
    return token


def parse_bundle_uri_response(data: bytes) -> Optional[BundleUriList]:
    """Parse one complete protocol-v2 bundle list.

    ``None`` means that the optional list is not usable and callers should
    continue with the ordinary Git fetch path.  This graceful degradation is a
    core bundle-uri protocol requirement.  Framing violations still raise,
    because they indicate a broken protocol-v2 response rather than merely bad
    optional bundle metadata.
    """

    pairs = _parse_bundle_uri_pairs(data)

    version = 1
    mode = "all"
    heuristic: Optional[str] = None
    bundle_state: Dict[str, Dict[str, object]] = {}

    for key, value in pairs:
        lowered = key.lower()
        if lowered == "bundle.version":
            try:
                parsed_version = int(value, 10)
            except ValueError:
                return None
            if parsed_version != 1:
                return None
            version = parsed_version
            continue

        if lowered == "bundle.mode":
            normalized_mode = value.lower()
            if normalized_mode not in {"all", "any"}:
                return None
            mode = normalized_mode
            continue

        if lowered == "bundle.heuristic":
            # Unknown heuristics are explicitly ignorable.  Keep only the one
            # currently documented by Git.
            heuristic = "creationToken" if value == "creationToken" else None
            continue

        if not lowered.startswith("bundle."):
            continue

        remainder = key[len("bundle.") :]
        if "." not in remainder:
            continue
        bundle_id, subkey = remainder.split(".", 1)
        if not _BUNDLE_ID_RE.fullmatch(bundle_id):
            continue
        if not subkey:
            continue

        state = bundle_state.setdefault(bundle_id, {})
        normalized_subkey = subkey.lower()
        if normalized_subkey == "uri":
            # Git treats a second URI for one bundle id as a malformed list.
            if "uri" in state:
                return None
            state["uri"] = value
        elif normalized_subkey == "creationtoken":
            token = _creation_token(value)
            if token is not None:
                state["creation_token"] = token
        elif normalized_subkey == "filter":
            state["filter_spec"] = value
        elif normalized_subkey == "location":
            state["location"] = value
        else:
            # Future per-bundle hints are intentionally ignored after creating
            # the bundle id, matching Git's forward-compatible list model.
            pass

    bundles = []
    for bundle_id in sorted(bundle_state):
        state = bundle_state[bundle_id]
        uri = state.get("uri")
        if not isinstance(uri, str) or not uri:
            # A named bundle without its required URI makes the optional list
            # unusable, but it must not break ordinary clone/fetch.
            return None
        bundles.append(
            BundleUriEntry(
                bundle_id=bundle_id,
                uri=uri,
                filter_spec=(
                    state.get("filter_spec")
                    if isinstance(state.get("filter_spec"), str)
                    else None
                ),
                creation_token=(
                    state.get("creation_token")
                    if isinstance(state.get("creation_token"), int)
                    else None
                ),
                location=(
                    state.get("location")
                    if isinstance(state.get("location"), str)
                    else None
                ),
            )
        )

    return BundleUriList(
        version=version,
        mode=mode,
        heuristic=heuristic,
        bundles=tuple(bundles),
    )


class SmartHttpV2BundleUriClient(SmartHttpV2QueryClient):
    """Discover server-advertised bundle metadata without following its URIs."""

    def discover_bundle_uris(self) -> Optional[BundleUriList]:
        capabilities = self.discover_capabilities()
        if capabilities is None or not capabilities.supports("bundle-uri"):
            return None

        body = build_bundle_uri_request(
            capabilities,
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
                context="bundle-uri response",
            )
            return parse_bundle_uri_response(response.read())
