"""Phase 104 tests: SHA-256 multi-pack-index storage and lookup."""

from __future__ import annotations

import hashlib
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from pygit import Repository
from pygit.multi_pack_index import (
    parse_multi_pack_index,
    parse_multi_pack_index_bytes,
    verify_multi_pack_index,
    write_multi_pack_index,
)
from pygit.objects import BlobObject
from pygit.pack import PackWriter


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def _pack(repo: Repository, *objects: BlobObject):
    pairs = []
    for obj in objects:
        oid = repo.store.write(obj)
        pairs.append((oid, obj))
    pack_path, idx_path = PackWriter(pairs).write_pack_and_idx(
        repo.pygit_dir / "objects" / "pack"
    )
    return pack_path, idx_path, [oid for oid, _ in pairs]


def _run(repo: Repository, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def _rewrite_checksum(data: bytearray) -> None:
    data[-32:] = hashlib.sha256(data[:-32]).digest()


def test_write_parse_and_lookup_multiple_packs(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _, first_idx, first_oids = _pack(repo, BlobObject(b"first\n"))
    _, second_idx, second_oids = _pack(repo, BlobObject(b"second\n"))

    path = write_multi_pack_index(repo.pygit_dir / "objects" / "pack")
    parsed = parse_multi_pack_index(path)

    assert path.name == "multi-pack-index"
    assert parsed.version == 1
    assert parsed.hash_version == 2
    assert parsed.pack_names == tuple(sorted((first_idx.name, second_idx.name)))
    assert parsed.object_count == 2
    assert parsed.fanout[-1] == 2
    assert {entry.oid for entry in parsed.entries} == set(first_oids + second_oids)
    assert parsed.lookup(first_oids[0]).pack_name == first_idx.name
    assert parsed.lookup(second_oids[0]).pack_name == second_idx.name
    assert parsed.lookup("f" * 64) is None
    assert parsed.lookup("not-an-object") is None


def test_duplicate_object_chooses_first_pack_deterministically(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    shared = BlobObject(b"shared\n")
    unique_a = BlobObject(b"only-a\n")
    unique_b = BlobObject(b"only-b\n")

    _, idx_a, oids_a = _pack(repo, shared, unique_a)
    _, idx_b, oids_b = _pack(repo, shared, unique_b)
    shared_oid = oids_a[0]
    assert shared_oid == oids_b[0]

    parsed = parse_multi_pack_index(
        write_multi_pack_index(repo.pygit_dir / "objects" / "pack")
    )
    assert parsed.object_count == 3
    assert parsed.lookup(shared_oid).pack_name == min(idx_a.name, idx_b.name)


def test_parser_rejects_checksum_corruption(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _pack(repo, BlobObject(b"checksum\n"))
    path = write_multi_pack_index(repo.pygit_dir / "objects" / "pack")
    data = bytearray(path.read_bytes())
    data[-1] ^= 0x01

    with pytest.raises(ValueError, match="checksum mismatch"):
        parse_multi_pack_index_bytes(bytes(data))


def test_parser_rejects_invalid_pack_id_with_valid_checksum(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _pack(repo, BlobObject(b"bad-pack-id\n"))
    path = write_multi_pack_index(repo.pygit_dir / "objects" / "pack")
    data = bytearray(path.read_bytes())

    # Fourth chunk-table record is OOFF; overwrite its first pack-id field.
    record_pos = 12 + 3 * 12
    ooff_start = struct.unpack(">Q", data[record_pos + 4 : record_pos + 12])[0]
    data[ooff_start : ooff_start + 4] = struct.pack(">I", 99)
    _rewrite_checksum(data)

    with pytest.raises(ValueError, match="invalid pack id"):
        parse_multi_pack_index_bytes(bytes(data))


def test_verify_checks_current_source_indexes_and_pack_pairs(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    pack_path, _, _ = _pack(repo, BlobObject(b"verify-source\n"))
    path = write_multi_pack_index(repo.pygit_dir / "objects" / "pack")

    verified = verify_multi_pack_index(path)
    assert verified.object_count == 1

    pack_path.unlink()
    with pytest.raises(FileNotFoundError) as excinfo:
        verify_multi_pack_index(path)
    assert str(pack_path) in str(excinfo.value)


def test_object_store_uses_midx_fast_path_for_covered_packs(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _, first_idx, first_oids = _pack(repo, BlobObject(b"target\n"))
    _, second_idx, second_oids = _pack(repo, BlobObject(b"other\n"))
    write_multi_pack_index(repo.pygit_dir / "objects" / "pack")

    target_oid = first_oids[0]
    assert repo.store.delete(target_oid)
    assert repo.store.delete(second_oids[0])

    # Corrupt an unrelated covered index. The direct MIDX mapping for the
    # target must avoid parsing every covered .idx file on each lookup.
    assert second_idx != first_idx
    data = bytearray(second_idx.read_bytes())
    data[-1] ^= 0x01
    second_idx.write_bytes(data)

    obj = repo.store.read(target_oid)
    assert isinstance(obj, BlobObject)
    assert obj.data == b"target\n"
    assert repo.store.exists(target_oid)


def test_stale_midx_falls_back_to_pack_added_after_write(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _, _, first_oids = _pack(repo, BlobObject(b"before-midx\n"))
    write_multi_pack_index(repo.pygit_dir / "objects" / "pack")
    assert repo.store.delete(first_oids[0])

    _, _, second_oids = _pack(repo, BlobObject(b"after-midx\n"))
    assert repo.store.delete(second_oids[0])

    first = repo.store.read(first_oids[0])
    second = repo.store.read(second_oids[0])
    assert first.data == b"before-midx\n"
    assert second.data == b"after-midx\n"
    assert {first_oids[0], second_oids[0]} <= set(repo.store.all_shas())
    assert repo.store.resolve_prefix(second_oids[0][:16]) == second_oids[0]


def test_cli_write_and_verify_round_trip(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _pack(repo, BlobObject(b"cli\n"))

    written = _run(repo, "multi-pack-index", "write")
    assert written.returncode == 0, written.stderr
    assert written.stdout == ""
    assert written.stderr == ""
    path = repo.pygit_dir / "objects" / "pack" / "multi-pack-index"
    assert path.is_file()

    verified = _run(repo, "multi-pack-index", "verify")
    assert verified.returncode == 0, verified.stderr
    assert verified.stdout == ""
    assert verified.stderr == ""


def test_cli_reports_missing_packs_and_exposes_subcommands(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    failed = _run(repo, "multi-pack-index", "write")
    assert failed.returncode == 1
    assert "without pack indexes" in failed.stderr

    help_result = _run(repo, "multi-pack-index", "--help")
    assert help_result.returncode == 0, help_result.stderr
    assert "write" in help_result.stdout
    assert "verify" in help_result.stdout
