"""Phase 66 tests: hardened index-pack and unpack-objects plumbing."""

from __future__ import annotations

import hashlib
import struct
import zlib
from pathlib import Path

import pytest

from pygit import Repository, index_pack, parse_pack, parse_pack_bytes, unpack_objects
from pygit.objects import BlobObject, CommitObject, Identity, TagObject, TreeEntry, TreeObject
from pygit.pack import PackReader, PackWriter
from pygit.pack_cli import run_index_pack, run_unpack_objects
from pygit.pack_verifier import verify_packfile


def _sample_pack(tmp_path: Path):
    repo = Repository.init(str(tmp_path / "source"))
    ident = Identity("Tester", "tester@example.com", timestamp=1, timezone="+0000")
    blob = BlobObject(b"hello pack\n")
    blob_oid = repo.store.write(blob)
    tree = TreeObject([TreeEntry("100644", "hello.txt", blob_oid)])
    tree_oid = repo.store.write(tree)
    commit = CommitObject(tree=tree_oid, parents=[], author=ident, committer=ident, message="packed commit")
    commit_oid = repo.store.write(commit)
    tag = TagObject(target_sha=commit_oid, target_type=b"commit", tag_name="v1", tagger=ident, message="packed tag")
    tag_oid = repo.store.write(tag)
    objects = [(blob_oid, blob), (tree_oid, tree), (commit_oid, commit), (tag_oid, tag)]
    pack_path, idx_path = PackWriter(objects).write_pack_and_idx(tmp_path / "packs", "sample")
    return pack_path, idx_path, {blob_oid, tree_oid, commit_oid, tag_oid}


def _with_checksum(data: bytearray) -> bytes:
    data[-32:] = hashlib.sha256(data[:-32]).digest()
    return bytes(data)


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
    payload = b"PACK" + struct.pack(">II", 2, 1) + _entry_header(type_id, size) + zlib.compress(store_bytes)
    return payload + hashlib.sha256(payload).digest()


def test_parse_pack_validates_all_object_types(tmp_path: Path) -> None:
    pack_path, _, expected = _sample_pack(tmp_path)
    parsed = parse_pack(pack_path)
    assert parsed.version == 2
    assert len(parsed.checksum) == 64
    assert {entry.oid for entry in parsed.entries} == expected
    assert {entry.type_name for entry in parsed.entries} == {"blob", "tree", "commit", "tag"}
    assert all(entry.size > 0 and entry.compressed_size > 0 for entry in parsed.entries)


def test_index_pack_rebuilds_index_usable_by_reader_and_verifier(tmp_path: Path) -> None:
    pack_path, idx_path, expected = _sample_pack(tmp_path)
    idx_path.unlink()
    result = index_pack(pack_path)
    assert result.idx_path == idx_path and result.object_count == 4
    assert set(result.oids) == expected
    reader = PackReader(idx_path)
    assert set(reader.get_shas()) == expected
    assert {reader.read_object(oid).type_name for oid in expected} == {b"blob", b"tree", b"commit", b"tag"}
    assert {row[0] for row in verify_packfile(idx_path)} == expected
    with pytest.raises(FileExistsError):
        index_pack(pack_path)
    assert index_pack(pack_path, force=True).object_count == 4


def test_unpack_objects_materializes_exact_loose_objects(tmp_path: Path) -> None:
    pack_path, _, expected = _sample_pack(tmp_path)
    target = Repository.init(str(tmp_path / "target"))
    result = unpack_objects(target, pack_path)
    assert (result.object_count, result.written, result.existing) == (4, 4, 0)
    assert set(target.store.all_shas()) == expected
    assert {target.store.read(oid).type_name for oid in expected} == {b"blob", b"tree", b"commit", b"tag"}
    second = unpack_objects(target, pack_path)
    assert (second.written, second.existing) == (0, 4)


def test_unpack_dry_run_performs_no_writes(tmp_path: Path) -> None:
    pack_path, _, _ = _sample_pack(tmp_path)
    target = Repository.init(str(tmp_path / "target"))
    result = unpack_objects(target, pack_path, dry_run=True)
    assert (result.object_count, result.written, result.existing) == (4, 0, 0)
    assert target.store.all_shas() == []


def test_corrupt_pack_checksum_is_rejected_before_loose_writes(tmp_path: Path) -> None:
    pack_path, _, _ = _sample_pack(tmp_path)
    damaged = tmp_path / "damaged.pack"
    data = bytearray(pack_path.read_bytes())
    data[20] ^= 0x01
    damaged.write_bytes(data)
    target = Repository.init(str(tmp_path / "target"))
    with pytest.raises(ValueError, match="checksum"):
        unpack_objects(target, damaged)
    assert target.store.all_shas() == []


def test_bad_pack_version_and_trailing_data_are_rejected(tmp_path: Path) -> None:
    pack_path, _, _ = _sample_pack(tmp_path)
    original = pack_path.read_bytes()
    bad_version = bytearray(original)
    bad_version[4:8] = (3).to_bytes(4, "big")
    with pytest.raises(ValueError, match="version"):
        parse_pack_bytes(_with_checksum(bad_version))
    trailing = bytearray(original[:-32] + b"junk" + original[-32:])
    with pytest.raises(ValueError, match="trailing"):
        parse_pack_bytes(_with_checksum(trailing))


def test_underdeclared_zlib_entry_is_bounded_by_claimed_size(monkeypatch) -> None:
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


def test_structurally_incomplete_typed_object_is_rejected() -> None:
    data = _single_entry_pack(1, b"commit 0\x00")
    with pytest.raises(ValueError, match="invalid commit payload"):
        parse_pack_bytes(data)


def test_existing_corrupt_loose_object_blocks_unpack_before_other_writes(tmp_path: Path) -> None:
    pack_path, _, expected = _sample_pack(tmp_path)
    target = Repository.init(str(tmp_path / "target"))
    first = sorted(expected)[0]
    corrupt = target.store.root / first[:2] / first[2:]
    corrupt.parent.mkdir(parents=True, exist_ok=True)
    corrupt.write_bytes(b"not-zlib")
    with pytest.raises(ValueError, match="existing loose object"):
        unpack_objects(target, pack_path)
    loose = []
    for prefix in target.store.root.iterdir():
        if prefix.is_dir() and len(prefix.name) == 2:
            loose.extend(path for path in prefix.iterdir() if path.is_file())
    assert loose == [corrupt]


def test_cli_handlers_index_and_unpack(tmp_path: Path, monkeypatch, capsys) -> None:
    pack_path, idx_path, expected = _sample_pack(tmp_path)
    idx_path.unlink()
    assert run_index_pack([str(pack_path)]) == 0
    assert capsys.readouterr().out.strip().endswith(".idx")
    target = Repository.init(str(tmp_path / "target"))
    monkeypatch.chdir(target.worktree)
    capsys.readouterr()
    assert run_unpack_objects([str(pack_path)]) == 0
    assert capsys.readouterr().out.strip() == "objects 4; written 4; existing 0"
    assert set(target.store.all_shas()) == expected
