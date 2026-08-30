"""Bounded, all-verified orchestration for protocol-v2 external packfiles.

Phase320 composes the explicit Phase319 downloader across all descriptors from one
packfile-uris response without crossing the repository transaction boundary.
Every pack is independently downloaded, bounded, checksum-verified, and parsed;
callers receive a result only after the complete batch succeeds.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Tuple

from .protocol_v2_packfile_uri_download import (
    DownloadedPackfileUri,
    download_packfile_uri,
)
from .protocol_v2_packfile_uris import PackfileUriDescriptor
from .remote import NativeObject


_DEFAULT_MAX_PACK_BYTES = 256 * 1024 * 1024
_DEFAULT_MAX_TOTAL_BYTES = 512 * 1024 * 1024
_DEFAULT_MAX_PACKS = 64


@dataclass(frozen=True)
class DownloadedPackfileUriBatch:
    """A completely verified external-pack batch with a merged native object set."""

    downloads: Tuple[DownloadedPackfileUri, ...]
    objects: Dict[str, NativeObject]
    total_bytes: int


def _positive_integer(value: int, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"protocol-v2 packfile URI {name} must be a positive integer")
    return value


def download_packfile_uris(
    descriptors: Iterable[PackfileUriDescriptor],
    *,
    timeout: int = 30,
    max_pack_bytes: int = _DEFAULT_MAX_PACK_BYTES,
    max_total_bytes: int = _DEFAULT_MAX_TOTAL_BYTES,
    max_packs: int = _DEFAULT_MAX_PACKS,
    opener=None,
) -> DownloadedPackfileUriBatch:
    """Download and verify a complete descriptor batch before exposing results.

    Validation that is independent of network content happens before the first
    request.  The cumulative byte budget is enforced by constraining every
    Phase319 download to the smaller of the per-pack limit and the remaining
    batch allowance.

    This function deliberately has no repository side effects.  A failure after
    earlier network reads therefore exposes no partial batch result and leaves
    ref/object-store/promisor transaction handling to a later phase.
    """

    _positive_integer(timeout, "timeout")
    _positive_integer(max_pack_bytes, "max_pack_bytes")
    _positive_integer(max_total_bytes, "max_total_bytes")
    _positive_integer(max_packs, "max_packs")

    try:
        items = tuple(descriptors)
    except TypeError as exc:
        raise TypeError("protocol-v2 packfile URI descriptors must be iterable") from exc

    if not items:
        raise ValueError("protocol-v2 packfile URI batch must contain at least one descriptor")
    if len(items) > max_packs:
        raise ValueError("protocol-v2 packfile URI batch exceeds configured pack-count limit")
    if any(not isinstance(item, PackfileUriDescriptor) for item in items):
        raise TypeError("protocol-v2 packfile URI batch contains a non-descriptor value")

    pack_hashes = [item.pack_hash for item in items]
    if len(set(pack_hashes)) != len(pack_hashes):
        raise ValueError("protocol-v2 packfile URI batch contains duplicate pack checksums")

    downloads = []
    merged: Dict[str, NativeObject] = {}
    total = 0

    for descriptor in items:
        remaining = max_total_bytes - total
        if remaining <= 0:
            raise ValueError("protocol-v2 packfile URI batch exceeds configured total size limit")
        downloaded = download_packfile_uri(
            descriptor,
            timeout=timeout,
            max_bytes=min(max_pack_bytes, remaining),
            opener=opener,
        )
        total += len(downloaded.pack)
        if total > max_total_bytes:
            raise ValueError("protocol-v2 packfile URI batch exceeds configured total size limit")

        for oid, obj in downloaded.objects.items():
            previous = merged.get(oid)
            if previous is not None and previous != obj:
                raise ValueError(
                    "protocol-v2 external packs contain conflicting objects for one native OID"
                )
            merged[oid] = obj
        downloads.append(downloaded)

    return DownloadedPackfileUriBatch(tuple(downloads), merged, total)
