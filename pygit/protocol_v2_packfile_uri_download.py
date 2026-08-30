"""Explicit, checksum-verifying downloads for protocol-v2 packfile URIs.

Phase319 deliberately keeps downloading separate from Phase318 fetch parsing.
A caller must explicitly pass a previously parsed ``PackfileUriDescriptor`` to
this module.  Downloads are bounded, restricted to HTTP(S), verified against
both the pack's own SHA-1 trailer and the advertised pack checksum, and parsed
without mutating refs, the object store, or promisor metadata.
"""

from __future__ import annotations

import hashlib
import hmac
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Dict, Optional

from .protocol_v2_packfile_uris import PackfileUriDescriptor
from .remote import NativeObject, PackParser


_DEFAULT_MAX_PACK_BYTES = 256 * 1024 * 1024
_READ_CHUNK_BYTES = 64 * 1024


@dataclass(frozen=True)
class DownloadedPackfileUri:
    """A verified external native pack and its parsed remote-native objects."""

    descriptor: PackfileUriDescriptor
    final_url: str
    pack: bytes
    objects: Dict[str, NativeObject]


def _validated_download_url(descriptor: PackfileUriDescriptor) -> str:
    """Convert a descriptor URI to a safe urllib URL at the download boundary."""

    try:
        url = descriptor.uri.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError(
            "protocol-v2 packfile URI must be ASCII or percent-encoded before download"
        ) from exc

    parsed = urllib.parse.urlsplit(url)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValueError(
            f"unsupported protocol-v2 packfile URI download scheme: {scheme!r}"
        )
    if not parsed.hostname:
        raise ValueError("protocol-v2 packfile URI download requires a host")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(
            "protocol-v2 packfile URI download rejects embedded credentials"
        )
    return url


def _validate_final_url(original_url: str, final_url: str) -> None:
    """Reject redirects outside HTTP(S) and HTTPS-to-HTTP downgrades."""

    original = urllib.parse.urlsplit(original_url)
    final = urllib.parse.urlsplit(final_url)
    final_scheme = final.scheme.lower()
    if final_scheme not in {"http", "https"}:
        raise ValueError(
            "protocol-v2 packfile URI redirect left the allowed HTTP(S) schemes"
        )
    if not final.hostname:
        raise ValueError("protocol-v2 packfile URI redirect target requires a host")
    if final.username is not None or final.password is not None:
        raise ValueError(
            "protocol-v2 packfile URI redirect target contains embedded credentials"
        )
    if original.scheme.lower() == "https" and final_scheme != "https":
        raise ValueError("protocol-v2 packfile URI redirect downgraded HTTPS to HTTP")


def verify_packfile_uri_payload(
    descriptor: PackfileUriDescriptor,
    pack: bytes,
) -> Dict[str, NativeObject]:
    """Verify a downloaded native SHA-1 pack before exposing parsed objects.

    Git SHA-1 packs end in a 20-byte SHA-1 checksum over all preceding pack
    bytes.  The packfile-URI descriptor advertises the same checksum in hex.
    Verify both relationships independently before invoking ``PackParser``.
    """

    if not isinstance(pack, bytes):
        raise TypeError("downloaded protocol-v2 packfile URI payload must be bytes")
    if len(pack) < 32 or not pack.startswith(b"PACK"):
        raise ValueError("downloaded protocol-v2 packfile URI is not a native PACK")

    body = pack[:-20]
    trailer = pack[-20:]
    computed = hashlib.sha1(body).digest()
    if not hmac.compare_digest(trailer, computed):
        raise ValueError("downloaded protocol-v2 packfile URI has an invalid pack checksum")

    advertised = bytes.fromhex(descriptor.pack_hash)
    if not hmac.compare_digest(trailer, advertised):
        raise ValueError(
            "downloaded protocol-v2 packfile URI checksum does not match descriptor"
        )

    return PackParser(pack).parse()


def download_packfile_uri(
    descriptor: PackfileUriDescriptor,
    *,
    timeout: int = 30,
    max_bytes: int = _DEFAULT_MAX_PACK_BYTES,
    opener=None,
) -> DownloadedPackfileUri:
    """Explicitly download, bound, verify, and parse one external pack.

    This function has no repository side effects.  In particular it does not
    update refs, write to the object store, create keep files, or touch promisor
    metadata.  ``opener`` is injectable for deterministic tests; the default is
    ``urllib.request.urlopen``.
    """

    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        raise ValueError("protocol-v2 packfile URI timeout must be a positive integer")
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
        raise ValueError("protocol-v2 packfile URI max_bytes must be a positive integer")

    url = _validated_download_url(descriptor)
    open_url = opener or urllib.request.urlopen
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"Accept": "application/x-git-packed-objects"},
    )

    with open_url(request, timeout=timeout) as response:
        final_url = response.geturl() if hasattr(response, "geturl") else url
        _validate_final_url(url, final_url)

        headers = getattr(response, "headers", None)
        if headers is not None:
            raw_length: Optional[str] = headers.get("Content-Length")
            if raw_length is not None:
                try:
                    content_length = int(raw_length, 10)
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        "protocol-v2 packfile URI response has invalid Content-Length"
                    ) from exc
                if content_length < 0:
                    raise ValueError(
                        "protocol-v2 packfile URI response has negative Content-Length"
                    )
                if content_length > max_bytes:
                    raise ValueError(
                        "protocol-v2 packfile URI response exceeds configured size limit"
                    )

        chunks = []
        total = 0
        while True:
            chunk = response.read(min(_READ_CHUNK_BYTES, max_bytes - total + 1))
            if not chunk:
                break
            if not isinstance(chunk, bytes):
                raise TypeError("protocol-v2 packfile URI response read must return bytes")
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(
                    "protocol-v2 packfile URI response exceeds configured size limit"
                )
            chunks.append(chunk)

    pack = b"".join(chunks)
    objects = verify_packfile_uri_payload(descriptor, pack)
    return DownloadedPackfileUri(descriptor, final_url, pack, objects)
