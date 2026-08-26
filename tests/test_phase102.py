"""Phase 102 tests: strict ``verify-pack`` diagnostics."""

from __future__ import annotations

import hashlib
import struct
import subprocess
import sys
from pathlib import Path

from pygit import Repository
from pygit.objects import BlobObject, TreeObject
from pygit.pack import PackWriter
from pygit.pack_index import parse_index
from pygit.verify_pack import verify_pack


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def _pack(repo: Repository) -> tuple[Path, Path, list[str]]:
    blob = BlobObject(b"verify-pack\x00payload\n")
    tree = TreeObject()
    blob_oid = repo.store.write(blob)
    tree_oid = repo.store.write(tree)
    pack_path, idx_path = PackWriter(
        [(blob_oid, blob), (tree_oid, tree)]
    ).write_pack_and_idx(repo.pygit_dir / "objects" / "pack")
    return pack_path, idx_path, [blob_oid, tree_oid]


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", "verify-pack", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def _rewrite_trailer(path: Path) -> None:
    data = bytearray(path.read_bytes())
    data[-32:] = hashlib.sha256(data[:-32]).digest()
    path.write_bytes(data)


def test_api_fully_verifies_index_pack_and_objects(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    pack_path, idx_path, oids = _pack(repo)

    result = verify_pack(idx_path)
    assert result.idx_path == idx_path
    assert result.pack_path == pack_path
    assert result.object_count == 2
    assert {obj.oid for obj in result.objects} == set(oids)
    assert {obj.type_name for obj in result.objects} == {"blob", "tree"}
    assert all(obj.packed_size > 0 for obj in result.objects)
    assert all(obj.offset >= 12 for obj in result.objects)


def test_cli_default_reports_ok_after_full_verification(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _, idx_path, _ = _pack(repo)

    result = _run(str(idx_path))
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert result.stdout == f"{idx_path}: ok\n"


def test_cli_verbose_uses_non_delta_object_format(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _, idx_path, oids = _pack(repo)

    result = _run("--verbose", str(idx_path))
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    object_lines = lines[:2]
    assert {line.split()[0] for line in object_lines} == set(oids)
    assert {line.split()[1] for line in object_lines} == {"blob", "tree"}
    assert all(len(line.split()) == 5 for line in object_lines)
    assert lines[2] == "non delta: 2 objects"
    assert lines[3] == f"{idx_path}: ok"


def test_corrupt_index_checksum_is_rejected(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _, idx_path, _ = _pack(repo)
    data = bytearray(idx_path.read_bytes())
    data[-1] ^= 0x01
    idx_path.write_bytes(data)

    result = _run(str(idx_path))
    assert result.returncode == 1
    assert result.stdout == ""
    assert f"{idx_path}: bad" in result.stderr
    assert "pack index SHA-256 checksum mismatch" in result.stderr


def test_corrupt_pack_checksum_is_rejected(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    pack_path, idx_path, _ = _pack(repo)
    data = bytearray(pack_path.read_bytes())
    data[-1] ^= 0x01
    pack_path.write_bytes(data)

    result = _run(str(idx_path))
    assert result.returncode == 1
    assert "pack SHA-256 checksum mismatch" in result.stderr


def test_crc_corruption_is_detected_after_valid_index_checksum(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _, idx_path, _ = _pack(repo)
    index = parse_index(idx_path)
    data = bytearray(idx_path.read_bytes())
    crc_start = 8 + 256 * 4 + index.object_count * 64
    data[crc_start] ^= 0x01
    idx_path.write_bytes(data)
    _rewrite_trailer(idx_path)

    result = _run(str(idx_path))
    assert result.returncode == 1
    assert "CRC-32 mismatch" in result.stderr


def test_pack_count_mismatch_is_detected_with_valid_pack_checksum(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    pack_path, idx_path, _ = _pack(repo)
    data = bytearray(pack_path.read_bytes())
    data[8:12] = struct.pack(">I", 3)
    pack_path.write_bytes(data)
    _rewrite_trailer(pack_path)

    result = _run(str(idx_path))
    assert result.returncode == 1
    assert "pack/index object count mismatch" in result.stderr


def test_multiple_indexes_continue_after_one_failure(tmp_path: Path) -> None:
    first = _repo(tmp_path / "first")
    _, good_idx, _ = _pack(first)
    second = _repo(tmp_path / "second")
    _, bad_idx, _ = _pack(second)
    data = bytearray(bad_idx.read_bytes())
    data[-1] ^= 0x01
    bad_idx.write_bytes(data)

    result = _run(str(good_idx), str(bad_idx))
    assert result.returncode == 1
    assert f"{good_idx}: ok" in result.stdout
    assert f"{bad_idx}: bad" in result.stderr


def test_api_requires_index_path_and_missing_pair_fails(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    pack_path, idx_path, _ = _pack(repo)

    try:
        verify_pack(pack_path)
    except ValueError as exc:
        assert "expects an .idx file" in str(exc)
    else:
        raise AssertionError(".pack path should not be accepted")

    pack_path.unlink()
    result = _run(str(idx_path))
    assert result.returncode == 1
    assert str(pack_path) in result.stderr


def test_help_exposes_verbose_mode(tmp_path: Path) -> None:
    result = _run("--help")
    assert result.returncode == 0, result.stderr
    assert "--verbose" in result.stdout
    assert "PACK.idx" in result.stdout
