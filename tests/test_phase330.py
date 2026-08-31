import hashlib
from pathlib import Path

import pytest

from pygit.loose_object_map import (
    encode_loose_object_map,
    publish_staged_loose_object_map,
)
from pygit.objects.blob import BlobObject
from pygit.protocol_v2_packfile_uri_stage import StagedPackfileUriImport
from pygit.repo import Repository


def _u32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 4], "big")


def _u64(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 8], "big")


def test_lmap_v1_header_offsets_tables_and_sha256_trailer_match_git_format():
    mapping = {
        "00112233445566778899aabbccddeeff00112233": "10" * 32,
        "01112233445566778899aabbccddeeff00112233": "20" * 32,
    }
    data = encode_loose_object_map(mapping)

    assert data[:4] == b"LMAP"
    assert _u32(data, 4) == 1
    assert _u32(data, 8) == 60
    assert _u32(data, 12) == 2
    assert _u32(data, 16) == 2
    assert _u32(data, 20) == 0x73323536  # s256
    assert _u32(data, 36) == 0x73686131  # sha1
    assert _u32(data, 24) == 1
    assert _u32(data, 40) == 1

    storage_offset = _u64(data, 28)
    compat_offset = _u64(data, 44)
    trailer_offset = _u64(data, 52)
    storage_short = _u32(data, 24)
    compat_short = _u32(data, 40)
    # Git aligns the full-name / metadata tables, not the shortened-name
    # table's starting offset itself.
    assert (storage_offset + 2 * storage_short) % 4 == 0
    assert (compat_offset + 2 * compat_short) % 4 == 0
    assert trailer_offset % 4 == 0
    assert trailer_offset + 32 == len(data)
    assert hashlib.sha256(data[:trailer_offset]).digest() == data[trailer_offset:]

    # First-format full names are SHA-256-sorted and metadata type 1 means
    # "loose object" in Git's LMAP format.
    storage_full = storage_offset + 2 * storage_short
    assert data[storage_full : storage_full + 32] == bytes.fromhex("10" * 32)
    assert data[storage_full + 32 : storage_full + 64] == bytes.fromhex("20" * 32)
    metadata = storage_full + 64
    assert _u32(data, metadata) == 1
    assert _u32(data, metadata + 4) == 1


def test_lmap_uses_minimum_unambiguous_byte_prefix_length():
    mapping = {
        "11" * 20: "aa00" + "00" * 30,
        "22" * 20: "aa01" + "00" * 30,
        "33" * 20: "bb00" + "00" * 30,
    }
    data = encode_loose_object_map(mapping)

    # SHA-256 values aa00.. and aa01.. differ in their second byte, so two
    # bytes are needed; SHA-1 values differ immediately, so one byte suffices.
    assert _u32(data, 24) == 2
    assert _u32(data, 40) == 1


def test_lmap_compat_order_table_points_back_to_storage_order():
    mapping = {
        "30" * 20: "10" * 32,
        "10" * 20: "30" * 32,
        "20" * 20: "20" * 32,
    }
    data = encode_loose_object_map(mapping)
    count = 3
    compat_offset = _u64(data, 44)
    compat_short = _u32(data, 40)
    compat_full = compat_offset + count * compat_short
    order_offset = compat_full + count * 20

    # SHA-1 sorted order is 10,20,30; their corresponding local SHA-256
    # positions in storage-sorted order (10,20,30) are 2,1,0.
    assert [_u32(data, order_offset + i * 4) for i in range(count)] == [2, 1, 0]


def test_publish_staged_map_is_content_addressed_and_idempotent(tmp_path: Path):
    repo = Repository.init(str(tmp_path / "repo"))
    blob = BlobObject(b"phase330\n")
    local = repo.store.write(blob)
    canonical = blob._build_store_bytes()
    native = hashlib.sha1(canonical).hexdigest()
    staged = StagedPackfileUriImport({native: local}, (local,))

    first = publish_staged_loose_object_map(repo, staged)
    second = publish_staged_loose_object_map(repo, staged)

    assert first == second
    assert first.object_count == 1
    assert first.path.name == f"map-{first.checksum}.map"
    raw = first.path.read_bytes()
    assert hashlib.sha256(raw[:-32]).hexdigest() == first.checksum
    assert raw[-32:].hex() == first.checksum


def test_publish_revalidates_local_sha256_object_before_map_write(tmp_path: Path):
    repo = Repository.init(str(tmp_path / "repo"))
    staged = StagedPackfileUriImport({"1" * 40: "2" * 64}, ("2" * 64,))

    with pytest.raises(KeyError):
        publish_staged_loose_object_map(repo, staged)

    assert not (repo.pygit_dir / "objects" / "object-map").exists()


def test_rejects_noncanonical_hash_domains_and_aliases():
    with pytest.raises(ValueError, match="native SHA-1"):
        encode_loose_object_map({"A" * 40: "b" * 64})
    with pytest.raises(ValueError, match="local SHA-256"):
        encode_loose_object_map({"a" * 40: "B" * 64})
    with pytest.raises(ValueError, match="multiple native"):
        encode_loose_object_map({"1" * 40: "a" * 64, "2" * 40: "a" * 64})


def test_empty_map_fails_closed():
    with pytest.raises(ValueError, match="at least one"):
        encode_loose_object_map({})
