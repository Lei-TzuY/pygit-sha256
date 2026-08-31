from pathlib import Path

import pytest

from pygit.loose_object_map import (
    decode_loose_object_map,
    encode_loose_object_map,
    lookup_local_sha256,
    lookup_native_sha1,
    publish_staged_loose_object_map,
    read_loose_object_maps,
)
from pygit.objects.blob import BlobObject
from pygit.protocol_v2_packfile_uri_stage import StagedPackfileUriImport
from pygit.repo import Repository


def test_decode_round_trip_preserves_sha1_sha256_identity_domains():
    mapping = {
        "10" * 20: "30" * 32,
        "20" * 20: "10" * 32,
        "30" * 20: "20" * 32,
    }
    decoded = decode_loose_object_map(encode_loose_object_map(mapping))

    assert decoded.native_to_local == mapping
    assert decoded.object_count == 3
    assert len(decoded.checksum) == 64


def test_decode_rejects_corrupt_trailer_and_noncanonical_prefix_width():
    mapping = {"11" * 20: "aa00" + "00" * 30, "22" * 20: "aa01" + "00" * 30}
    encoded = bytearray(encode_loose_object_map(mapping))
    encoded[-1] ^= 1
    with pytest.raises(ValueError, match="checksum"):
        decode_loose_object_map(bytes(encoded))

    encoded = bytearray(encode_loose_object_map(mapping))
    encoded[24:28] = (3).to_bytes(4, "big")
    import hashlib
    trailer = int.from_bytes(encoded[52:60], "big")
    encoded[trailer:] = hashlib.sha256(encoded[:trailer]).digest()
    with pytest.raises(ValueError):
        decode_loose_object_map(bytes(encoded))


def test_decode_rejects_wrong_algorithm_ids_even_with_valid_checksum():
    import hashlib

    encoded = bytearray(encode_loose_object_map({"11" * 20: "22" * 32}))
    encoded[36:40] = (0x73323536).to_bytes(4, "big")
    trailer = int.from_bytes(encoded[52:60], "big")
    encoded[trailer:] = hashlib.sha256(encoded[:trailer]).digest()
    with pytest.raises(ValueError, match="SHA-256 storage to SHA-1"):
        decode_loose_object_map(bytes(encoded))


def test_repository_lookup_reads_published_git_maps(tmp_path: Path):
    repo = Repository.init(str(tmp_path / "repo"))
    blob = BlobObject(b"phase332\n")
    local = repo.store.write(blob)
    import hashlib
    native = hashlib.sha1(blob._build_store_bytes()).hexdigest()
    staged = StagedPackfileUriImport({native: local}, (local,))
    published = publish_staged_loose_object_map(repo, staged)

    maps = read_loose_object_maps(repo)
    assert len(maps) == 1
    assert maps[0].checksum == published.checksum
    assert lookup_native_sha1(repo, local) == native
    assert lookup_local_sha256(repo, native) == local


def test_missing_mapping_returns_none_without_synthesizing_identity(tmp_path: Path):
    repo = Repository.init(str(tmp_path / "repo"))
    assert lookup_native_sha1(repo, "1" * 64) is None
    assert lookup_local_sha256(repo, "2" * 40) is None


def test_repository_reader_rejects_filename_checksum_mismatch(tmp_path: Path):
    repo = Repository.init(str(tmp_path / "repo"))
    directory = repo.pygit_dir / "objects" / "object-map"
    directory.mkdir(parents=True)
    (directory / ("map-" + "0" * 64 + ".map")).write_bytes(
        encode_loose_object_map({"1" * 40: "2" * 64})
    )

    with pytest.raises(ValueError, match="filename"):
        read_loose_object_maps(repo)


def test_repository_reader_rejects_cross_file_native_conflict(tmp_path: Path):
    repo = Repository.init(str(tmp_path / "repo"))
    directory = repo.pygit_dir / "objects" / "object-map"
    directory.mkdir(parents=True)

    import hashlib
    first = encode_loose_object_map({"1" * 40: "2" * 64})
    second = encode_loose_object_map({"1" * 40: "3" * 64})
    for data in (first, second):
        checksum = hashlib.sha256(data[:-32]).hexdigest()
        (directory / f"map-{checksum}.map").write_bytes(data)

    with pytest.raises(ValueError, match="conflicting local"):
        read_loose_object_maps(repo)


def test_lookup_rejects_noncanonical_hash_domains(tmp_path: Path):
    repo = Repository.init(str(tmp_path / "repo"))
    with pytest.raises(ValueError, match="local SHA-256"):
        lookup_native_sha1(repo, "A" * 64)
    with pytest.raises(ValueError, match="native SHA-1"):
        lookup_local_sha256(repo, "B" * 40)
