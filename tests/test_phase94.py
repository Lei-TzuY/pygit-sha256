"""Phase 94 tests: ``cat-file %(objectsize:disk)`` storage metrics."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pygit import Repository
from pygit.cat_file import batch_all_objects, format_batch_object, inspect_object, object_disk_size
from pygit.objects import BlobObject
from pygit.pack import PackWriter
from pygit.pack_index import parse_index


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def _loose_path(repo: Repository, oid: str) -> Path:
    return repo.store.root / oid[:2] / oid[2:]


def _pack(repo: Repository, *objects: BlobObject, prefix: str = "phase94") -> tuple[Path, Path]:
    pairs = [(obj.hash(), obj) for obj in objects]
    return PackWriter(pairs).write_pack_and_idx(repo.store.root / "pack", prefix)


def _packed_entry_size(pack_path: Path, idx_path: Path, oid: str) -> int:
    index = parse_index(idx_path)
    offsets = sorted(entry.offset for entry in index.entries)
    offset = next(entry.offset for entry in index.entries if entry.oid == oid)
    later = [candidate for candidate in offsets if candidate > offset]
    payload_end = pack_path.stat().st_size - 32
    return (min(later) if later else payload_end) - offset


def _run(
    repo: Repository,
    *args: str,
    input_data: bytes = b"",
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", "cat-file", *args],
        cwd=repo.worktree,
        input=input_data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_loose_disk_size_is_compressed_loose_file_size(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    oid = repo.store.write(BlobObject((b"compressible payload\n" * 64)))
    expected = _loose_path(repo, oid).stat().st_size

    record = inspect_object(repo, oid)
    assert record.disk_size == expected
    assert object_disk_size(repo, oid) == expected
    assert record.disk_size != record.size


def test_packed_only_disk_size_is_exact_pack_entry_width(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first = BlobObject(b"first packed payload\n" * 16)
    second = BlobObject(b"second packed payload\n" * 32)
    first_oid = repo.store.write(first)
    second_oid = repo.store.write(second)
    pack_path, idx_path = _pack(repo, first, second)
    _loose_path(repo, first_oid).unlink()
    _loose_path(repo, second_oid).unlink()

    assert object_disk_size(repo, first_oid) == _packed_entry_size(pack_path, idx_path, first_oid)
    assert object_disk_size(repo, second_oid) == _packed_entry_size(pack_path, idx_path, second_oid)
    assert inspect_object(repo, first_oid).disk_size == _packed_entry_size(pack_path, idx_path, first_oid)


def test_loose_copy_wins_when_object_is_also_packed(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    obj = BlobObject(b"duplicate storage copy\n" * 40)
    oid = repo.store.write(obj)
    loose_size = _loose_path(repo, oid).stat().st_size
    pack_path, idx_path = _pack(repo, obj)
    packed_size = _packed_entry_size(pack_path, idx_path, oid)

    assert object_disk_size(repo, oid) == loose_size
    assert inspect_object(repo, oid).disk_size == loose_size
    assert packed_size > 0


def test_custom_batch_format_expands_disk_size(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    oid = repo.store.write(BlobObject(b"format me\n" * 10))
    record = inspect_object(repo, oid)
    fmt = "%(objectname)|%(objectsize)|%(objectsize:disk)|%(objecttype)"

    assert format_batch_object(repo, oid, format_string=fmt) == (
        f"{oid}|{record.size}|{record.disk_size}|blob\n".encode("ascii")
    )

    result = _run(repo, f"--batch-check={fmt}", input_data=(oid + "\n").encode("ascii"))
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert result.stdout == f"{oid}|{record.size}|{record.disk_size}|blob\n".encode("ascii")


def test_disk_size_composes_with_nul_framing(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    oid = repo.store.write(BlobObject(b"nul framed\n" * 8))
    disk_size = object_disk_size(repo, oid)

    result = _run(
        repo,
        "--batch-check=%(objectsize:disk)",
        "-Z",
        input_data=oid.encode("ascii") + b"\0",
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert result.stdout == str(disk_size).encode("ascii") + b"\0"


def test_disk_size_composes_with_all_object_enumeration(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    loose = BlobObject(b"loose object\n")
    packed = BlobObject(b"packed object\n" * 24)
    loose_oid = repo.store.write(loose)
    packed_oid = repo.store.write(packed)
    _pack(repo, packed)
    _loose_path(repo, packed_oid).unlink()

    fmt = "%(objectname) %(objectsize:disk)"
    expected = b"".join(
        format_batch_object(repo, oid, format_string=fmt)
        for oid in sorted((loose_oid, packed_oid))
    )
    assert b"".join(batch_all_objects(repo, format_string=fmt)) == expected

    result = _run(repo, f"--batch-check={fmt}", "--batch-all-objects")
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert result.stdout == expected


def test_disk_size_missing_object_uses_canonical_missing_record(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    result = _run(
        repo,
        "--batch-check=%(objectsize:disk)",
        input_data=b"definitely-missing\n",
    )
    assert result.returncode == 0
    assert result.stdout == b"definitely-missing missing\n"
