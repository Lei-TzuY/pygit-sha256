"""Phase 66 regression tests for untrusted pack-entry validation."""

from __future__ import annotations

import hashlib
import struct
import zlib

import pytest

from pygit import parse_pack_bytes


def _entry_header(type_id: int, size: int) -> bytes:
    first = ((type_id & 0x07) << 4) | (size & 0x0F)
    size >>= 4
    out = bytearray()
    if size:
        first |= 0x80
    out.append(first)
    while size:
        value = size & 0x7F
        size >>= 7
        if size:
            value |= 0x80
        out.append(value)
    return bytes(out)


def _single_entry_pack(type_id: int, store_bytes: bytes, *, declared_size: int | None = None) -> bytes:
    size = len(store_bytes) if declared_size is None else declared_size
    payload = (
        b"PACK"
        + struct.pack(">II", 2, 1)
        + _entry_header(type_id, size)
        + zlib.compress(store_bytes)
    )
    return payload + hashlib.sha256(payload).digest()


def test_underdeclared_entry_uses_bounded_decompression(monkeypatch) -> None:
    store_bytes = b"blob 1048576\x00" + (b"A" * 1048576)
    data = _single_entry_pack(3, store_bytes, declared_size=16)
    real_decompressobj = zlib.decompressobj
    limits: list[int] = []

    class TrackingDecompressor:
        def __init__(self) -> None:
            self.inner = real_decompressobj()

        def decompress(self, compressed: bytes, max_length: int = 0) -> bytes:
            limits.append(max_length)
            return self.inner.decompress(compressed, max_length)

        def __getattr__(self, name: str):
            return getattr(self.inner, name)

    monkeypatch.setattr("pygit.pack_plumbing.zlib.decompressobj", TrackingDecompressor)

    with pytest.raises(ValueError, match="expands beyond"):
        parse_pack_bytes(data)
    assert limits == [17]


def test_noncanonical_object_envelope_is_rejected() -> None:
    data = _single_entry_pack(3, b"blob 03\x00abc")
    with pytest.raises(ValueError, match="non-canonical"):
        parse_pack_bytes(data)


def test_structurally_incomplete_commit_is_rejected() -> None:
    data = _single_entry_pack(1, b"commit 0\x00")
    with pytest.raises(ValueError, match="invalid commit payload"):
        parse_pack_bytes(data)
