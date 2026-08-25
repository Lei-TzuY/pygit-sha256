"""Phase 91 tests: strict full-pair ``verify-pack`` validation."""

from __future__ import annotations

import hashlib
import struct
import zlib
from pathlib import Path

import pytest

from pygit.objects import BlobObject
from pygit.pack import PackWriter
from pygit.pack_verifier import verify_packfile
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


def _index_bytes(entries: list[tuple[str, int, int]]) -> bytes:
    ordered = sorted(entries, key=lambda item: item[0])
    out = bytearray(b"\xfftOc" + struct.pack(">I", 2))
    counts = [0] * 256
    for oid, _, _ in ordered:
        counts[int(oid[:2], 16)] += 1
    cumulative = 0
    for count in counts:
        cumulative += count
        out.extend(struct.pack(">I", cumulative))
    for oid, _, _ in ordered:
        out.extend(oid.encode("ascii"))
    for _, crc, _ in ordered:
        out.extend(struct.pack(">I", crc))
    for _, _, offset in ordered:
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
) -> tuple[Path, Path, str, bytes]:
    actual_oid = hashlib.sha256(store_bytes).hexdigest()
    size = len(store_bytes) if declared_size is None else declared_size
    entry = _entry_header(type_id, size) + zlib.compress(store_bytes)
    payload = b"PACK" + struct.pack(">II", 2, 1) + entry
    pack_bytes = payload + hashlib.sha256(payload).digest()

    pack_path = tmp_path / "sample.pack"
    idx_path = tmp_path / "sample.idx"
    pack_path.write_bytes(pack_bytes)
    idx_path.write_bytes(
        _index_bytes(
            [
                (
                    index_oid or actual_oid,
                    (zlib.crc32(entry) & 0xFFFFFFFF)
                    if index_crc is None
                    else index_crc,
                    index_offset,
                )
            ]
        )
    )
    return pack_path, idx_path, actual_oid, entry


def test_valid_packwriter_pair_verifies_all_metadata(tmp_path: Path) -> None:
    first = BlobObject(b"first\n")
    second = BlobObject(b"second\n")
    _, idx_path = PackWriter(
        [(first.hash(), first), (second.hash(), second)]
    ).write_pack_and_idx(tmp_path, "valid")

    records = verify_packfile(idx_path, verbose=True)

    assert [record[0] for record in records] == sorted([first.hash(), second.hash()])
    assert all(record[1] == "blob" for record in records)
    assert all(record[2] > 0 for record in records)
    assert all(record[3] > 0 for record in records)
    assert all(record[4] >= 12 for record in records)


def test_corrupt_index_checksum_is_rejected(tmp_path: Path) -> None:
    blob = BlobObject(b"payload")
    _, idx_path, _, _ = _write_pair(tmp_path, blob._build_store_bytes())
    damaged = bytearray(idx_path.read_bytes())
    damaged[-1] ^= 0x01
    idx_path.write_bytes(damaged)

    with pytest.raises(ValueError, match="index SHA-256 checksum"):
        verify_packfile(idx_path)


def test_corrupt_pack_checksum_is_rejected(tmp_path: Path) -> None:
    blob = BlobObject(b"payload")
    pack_path, idx_path, _, _ = _write_pair(tmp_path, blob._build_store_bytes())
    damaged = bytearray(pack_path.read_bytes())
    damaged[-1] ^= 0x01
    pack_path.write_bytes(damaged)

    with pytest.raises(ValueError, match="pack SHA-256 checksum"):
        verify_packfile(idx_path)


def test_individually_valid_index_with_wrong_oid_is_rejected(tmp_path: Path) -> None:
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
        verify_packfile(idx_path)


def test_individually_valid_index_with_wrong_crc_is_rejected(tmp_path: Path) -> None:
    blob = BlobObject(b"payload")
    store_bytes = blob._build_store_bytes()
    _, idx_path, oid, entry = _write_pair(tmp_path, store_bytes)
    wrong_crc = (zlib.crc32(entry) ^ 0x01) & 0xFFFFFFFF
    idx_path.write_bytes(_index_bytes([(oid, wrong_crc, 12)]))

    with pytest.raises(ValueError, match="CRC-32 mismatch"):
        verify_packfile(idx_path)


def test_individually_valid_index_with_wrong_offset_is_rejected(tmp_path: Path) -> None:
    blob = BlobObject(b"payload")
    store_bytes = blob._build_store_bytes()
    _, idx_path, oid, entry = _write_pair(tmp_path, store_bytes)
    idx_path.write_bytes(
        _index_bytes([(oid, zlib.crc32(entry) & 0xFFFFFFFF, 13)])
    )

    with pytest.raises(ValueError, match="offset mismatch"):
        verify_packfile(idx_path)


def test_unknown_type_and_invalid_typed_payload_are_rejected(tmp_path: Path) -> None:
    blob = BlobObject(b"payload")
    _, idx_path, _, _ = _write_pair(
        tmp_path,
        blob._build_store_bytes(),
        type_id=7,
    )
    with pytest.raises(ValueError, match="unsupported packed object type id"):
        verify_packfile(idx_path)

    _, idx_path, _, _ = _write_pair(
        tmp_path,
        b"commit 0\x00",
        type_id=1,
    )
    with pytest.raises(ValueError, match="invalid commit payload"):
        verify_packfile(idx_path)


def test_underdeclared_entry_remains_bounded_through_verify_pack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blob = BlobObject(b"x" * (1024 * 1024))
    _, idx_path, _, _ = _write_pair(
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
        verify_packfile(idx_path)
    assert limits == [17]
