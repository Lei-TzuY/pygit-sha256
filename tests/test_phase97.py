"""Phase 97 tests: strict ``count-objects`` storage diagnostics."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pygit import Repository
from pygit.objects import BlobObject
from pygit.pack import PackWriter


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def _loose_path(repo: Repository, oid: str) -> Path:
    return repo.store.root / oid[:2] / oid[2:]


def _pack(repo: Repository, *objects: BlobObject, prefix: str = "phase97") -> tuple[Path, Path]:
    pairs = [(obj.hash(), obj) for obj in objects]
    return PackWriter(pairs).write_pack_and_idx(repo.store.root / "pack", prefix)


def _run(repo: Repository, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", "count-objects", *args],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def test_valid_loose_objects_are_counted_and_invalid_candidates_are_garbage(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    oid = repo.store.write(BlobObject(b"valid loose\n"))
    valid_size = _loose_path(repo, oid).stat().st_size

    bad_dir = repo.store.root / "aa"
    bad_dir.mkdir(parents=True, exist_ok=True)
    (bad_dir / ("b" * 62)).write_bytes(b"not-zlib")
    (bad_dir / "not-an-object").write_bytes(b"junk")

    info = repo.count_objects()
    assert info["count"] == 1
    assert info["size_bytes"] == valid_size
    assert info["garbage"] == 2
    assert info["size_garbage_bytes"] == len(b"not-zlib") + len(b"junk")


def test_hash_mismatched_loose_file_is_garbage_not_an_object(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    oid = repo.store.write(BlobObject(b"original\n"))
    original_path = _loose_path(repo, oid)
    compressed = original_path.read_bytes()

    forged_oid = "f" * 64
    forged_path = _loose_path(repo, forged_oid)
    forged_path.parent.mkdir(parents=True, exist_ok=True)
    forged_path.write_bytes(compressed)

    info = repo.count_objects()
    assert info["count"] == 1
    assert info["garbage"] == 1


def test_pack_statistics_and_prune_packable_use_validated_pack_pairs(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    duplicate = BlobObject(b"stored loose and packed\n" * 8)
    packed_only = BlobObject(b"packed only\n" * 16)
    duplicate_oid = repo.store.write(duplicate)
    packed_only_oid = repo.store.write(packed_only)
    pack_path, idx_path = _pack(repo, duplicate, packed_only)
    _loose_path(repo, packed_only_oid).unlink()

    info = repo.count_objects()
    assert info["count"] == 1
    assert info["in_pack"] == 2
    assert info["packs"] == 1
    assert info["prune_packable"] == 1
    assert info["size_pack_bytes"] == pack_path.stat().st_size + idx_path.stat().st_size
    assert duplicate_oid in repo.store.all_shas()


def test_orphan_and_invalid_pack_files_are_reported_as_garbage(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    pack_dir = repo.store.root / "pack"
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "orphan.idx").write_bytes(b"orphan-index")
    (pack_dir / "broken.idx").write_bytes(b"broken-index")
    (pack_dir / "broken.pack").write_bytes(b"broken-pack")
    (pack_dir / "random.tmp").write_bytes(b"tmp")

    info = repo.count_objects()
    assert info["packs"] == 0
    assert info["in_pack"] == 0
    assert info["garbage"] == 4
    assert info["size_garbage_bytes"] == sum(
        len(value)
        for value in (b"orphan-index", b"broken-index", b"broken-pack", b"tmp")
    )


def test_valid_pack_sidecars_are_not_garbage(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    obj = BlobObject(b"sidecar\n")
    repo.store.write(obj)
    pack_path, idx_path = _pack(repo, obj, prefix="pack-sidecar")
    (pack_path.parent / "pack-sidecar.keep").write_text("keep\n", encoding="utf-8")
    (pack_path.parent / "pack-sidecar.bitmap").write_bytes(b"bitmap")

    info = repo.count_objects()
    assert info["packs"] == 1
    assert info["garbage"] == 0
    assert info["size_pack_bytes"] == pack_path.stat().st_size + idx_path.stat().st_size


def test_alternates_are_reported_as_absolute_paths(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    alternate = tmp_path / "alternate-objects"
    alternate.mkdir()
    info_dir = repo.store.root / "info"
    info_dir.mkdir(parents=True, exist_ok=True)
    relative = Path("../../../alternate-objects")
    (info_dir / "alternates").write_text(str(relative) + "\n", encoding="utf-8")

    info = repo.count_objects()
    assert info["alternates"] == [str((repo.store.root / relative).resolve())]


def test_verbose_cli_reports_native_diagnostic_fields(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    obj = BlobObject(b"duplicate\n" * 20)
    repo.store.write(obj)
    _pack(repo, obj)
    (repo.store.root / "garbage-file").write_bytes(b"garbage")

    result = _run(repo, "-v")
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[:8] == [
        "count: 1",
        f"size: {repo.count_objects()['size_kb']}",
        "in-pack: 1",
        "packs: 1",
        f"size-pack: {repo.count_objects()['size_pack_kb']}",
        "prune-packable: 1",
        "garbage: 1",
        f"size-garbage: {repo.count_objects()['size_garbage_kb']}",
    ]


def test_human_readable_cli_uses_binary_units(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    repo.store.write(BlobObject(b"x" * 4096))

    normal = _run(repo, "-H")
    assert normal.returncode == 0, normal.stderr
    assert normal.stdout.startswith("1 objects, ")
    assert any(unit in normal.stdout for unit in ("bytes", "KiB", "MiB"))

    verbose = _run(repo, "-v", "-H")
    assert verbose.returncode == 0, verbose.stderr
    assert "size: " in verbose.stdout
    assert "size-pack: 0 bytes" in verbose.stdout
    assert "size-garbage: 0 bytes" in verbose.stdout


def test_count_objects_help_exposes_verbose_and_human_readable(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    result = _run(repo, "--help")
    assert result.returncode == 0, result.stderr
    assert "--verbose" in result.stdout
    assert "--human-readable" in result.stdout
