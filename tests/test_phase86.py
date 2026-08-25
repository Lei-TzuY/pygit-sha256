"""Phase 86 tests: strict, bounded random-access pack reads."""

from __future__ import annotations

import hashlib
import struct
import zlib
from pathlib import Path

import pytest

from pygit.objects import BlobObject
from pygit.pack import PackReader, PackWriter
import pygit.pack_plumbing as pack_plumbing


def _entry_header(type_id: int, size: int) -> bytes:
    first = ((type_id & 0x07) << 4) | (size & 0x0F)
    size >>= 4
    out = bytearray()
    if size:
        first |= 0x80
    out.append(first)
    while size:
        byte = size & 0x7F
        size >>= 7
        if size:
            byte |= 0x80
        out.append(byte)
    return bytes(out)


def _index_bytes(oid: str, crc32: int, offset: int) -> bytes:
    out = bytearray(b"\xfftOc" + struct.pack(">I", 2))
    bucket = int(oid[:2], 16)
    for index in range(256):
        out.extend(struct.pack(">I", 1 if index >= bucket else 0))
    out.extend(oid.encode("ascii"))
    out.extend(struct.pack(">I", crc32))
    out.extend(struct.pack(">I", offset))
    out.extend(hashlib.sha256(out).digest())
    return bytes(out)


def _write_pair(
    tmp_path: Path,
    store_bytes: bytes,
    *,
    type_id: int = 3,
    declared_size: int | None = None,
    index_oid: str | None = None,
    index_crc: int | None = None,
    index_offset: int = 12,
    pack_count: int = 1,
) -> tuple[Path, Path, str, bytes]:
    actual_oid = hashlib.sha256(store_bytes).hexdigest()
    outer_size = len(store_bytes) if declared_size is None else declared_size
    entry = _entry_header(type_id, outer_size) + zlib.compress(store_bytes)
    pack_payload = b"PACK" + struct.pack(">II", 2, pack_count) + entry
    pack_bytes = pack_payload + hashlib.sha256(pack_payload).digest()

    pack_path = tmp_path / "sample.pack"
    idx_path = tmp_path / "sample.idx"
    pack_path.write_bytes(pack_bytes)
    idx_path.write_bytes(
        _index_bytes(
            index_oid or actual_oid,
            (zlib.crc32(entry) & 0xFFFFFFFF) if index_crc is None else index_crc,
            index_offset,
        )
    )
    return pack_path, idx_path, actual_oid, entry


def test_valid_multi_entry_pack_reads_round_trip(tmp_path: Path) -> None:
    first = BlobObject(b"first\n")
    second = BlobObject(b"second\n")
    _, idx_path = PackWriter(
        [(first.hash(), first), (second.hash(), second)]
    ).write_pack_and_idx(tmp_path, "valid")

    reader = PackReader(idx_path)
    decoded_first = reader.read_object(first.hash())
    decoded_second = reader.read_object(second.hash())

    assert isinstance(decoded_first, BlobObject)
    assert isinstance(decoded_second, BlobObject)
    assert decoded_first.data == b"first\n"
    assert decoded_second.data == b"second\n"


def test_pack_checksum_and_count_are_validated_before_decode(tmp_path: Path) -> None:
    blob = BlobObject(b"payload")
    pack_path, idx_path, oid, _ = _write_pair(tmp_path, blob._build_store_bytes())

    damaged = bytearray(pack_path.read_bytes())
    damaged[-1] ^= 0x01
    pack_path.write_bytes(damaged)
    with pytest.raises(ValueError, match="checksum"):
        PackReader(idx_path).read_object(oid)

    pack_path, idx_path, oid, _ = _write_pair(
        tmp_path, blob._build_store_bytes(), pack_count=2
    )
    with pytest.raises(ValueError, match="object count mismatch"):
        PackReader(idx_path).read_object(oid)


def test_index_offset_must_fall_inside_pack_payload(tmp_path: Path) -> None:
    blob = BlobObject(b"payload")
    store_bytes = blob._build_store_bytes()
    header = _entry_header(3, len(store_bytes))
    entry = header + zlib.compress(store_bytes)
    payload_end = 12 + len(entry)
    _, idx_path, oid, _ = _write_pair(
        tmp_path,
        store_bytes,
        index_offset=payload_end,
    )

    with pytest.raises(ValueError, match="outside the pack payload"):
        PackReader(idx_path).read_object(oid)


def test_unknown_type_id_is_not_coerced_to_blob(tmp_path: Path) -> None:
    blob = BlobObject(b"payload")
    _, idx_path, oid, _ = _write_pair(
        tmp_path,
        blob._build_store_bytes(),
        type_id=7,
    )

    with pytest.raises(ValueError, match="unsupported packed object type id"):
        PackReader(idx_path).read_object(oid)


def test_index_crc_is_checked_for_requested_entry(tmp_path: Path) -> None:
    blob = BlobObject(b"payload")
    _, idx_path, oid, entry = _write_pair(tmp_path, blob._build_store_bytes())
    wrong_crc = (zlib.crc32(entry) ^ 0x01) & 0xFFFFFFFF
    idx_path.write_bytes(_index_bytes(oid, wrong_crc, 12))

    with pytest.raises(ValueError, match="CRC-32 mismatch"):
        PackReader(idx_path).read_object(oid)


def test_decoded_oid_must_match_indexed_oid(tmp_path: Path) -> None:
    blob = BlobObject(b"payload")
    store_bytes = blob._build_store_bytes()
    actual_oid = hashlib.sha256(store_bytes).hexdigest()
    fake_oid = ("0" if actual_oid[0] != "0" else "1") + actual_oid[1:]
    _, idx_path, _, _ = _write_pair(
        tmp_path,
        store_bytes,
        index_oid=fake_oid,
    )

    with pytest.raises(ValueError, match="object ID mismatch"):
        PackReader(idx_path).read_object(fake_oid)


def test_typed_payload_validation_is_reused(tmp_path: Path) -> None:
    invalid_commit = b"commit 0\x00"
    _, idx_path, oid, _ = _write_pair(
        tmp_path,
        invalid_commit,
        type_id=1,
    )

    with pytest.raises(ValueError, match="invalid commit payload"):
        PackReader(idx_path).read_object(oid)


def test_underdeclared_entry_uses_bounded_decompression(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blob = BlobObject(b"x" * (1024 * 1024))
    _, idx_path, oid, _ = _write_pair(
        tmp_path,
        blob._build_store_bytes(),
        declared_size=16,
    )

    real_decompressobj = pack_plumbing.zlib.decompressobj
    limits: list[int] = []

    class RecordingDecompressor:
        def __init__(self) -> None:
            self.inner = real_decompressobj()

        def decompress(self, data: bytes, max_length: int = 0) -> bytes:
            limits.append(max_length)
            return self.inner.decompress(data, max_length)

        def __getattr__(self, name: str):
            return getattr(self.inner, name)

    monkeypatch.setattr(
        pack_plumbing.zlib,
        "decompressobj",
        lambda: RecordingDecompressor(),
    )

    with pytest.raises(ValueError, match="expands beyond"):
        PackReader(idx_path).read_object(oid)
    assert limits == [17]
