"""Phase 70 tests: safe pruning of loose objects duplicated in packs."""

from __future__ import annotations

import io
import shutil
import zlib
from pathlib import Path

from pygit import Repository
from pygit.objects import BlobObject
from pygit.pack import PackWriter
from pygit.prune_packed import prune_packed
from pygit.prune_packed_cli import run_prune_packed


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def _loose_path(repo: Repository, oid: str) -> Path:
    return repo.store.root / oid[:2] / oid[2:]


def _pack(repo: Repository, *objects: BlobObject, prefix: str = "sample") -> tuple[Path, Path]:
    pairs = [(obj.hash(), obj) for obj in objects]
    return PackWriter(pairs).write_pack_and_idx(repo.store.root / "pack", prefix)


def test_prunes_verified_loose_duplicate_and_keeps_object_readable(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    obj = BlobObject(b"packed copy\n")
    oid = repo.store.write(obj)
    _pack(repo, obj)

    result = prune_packed(repo)

    assert result.pruned == 1
    assert result.oids == (oid,)
    assert not _loose_path(repo, oid).exists()
    assert repo.store.read(oid).data == b"packed copy\n"


def test_dry_run_reports_candidate_without_mutating_storage(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    obj = BlobObject(b"dry run\n")
    oid = repo.store.write(obj)
    _pack(repo, obj)

    result = prune_packed(repo, dry_run=True)

    assert result.pruned == 0
    assert result.oids == (oid,)
    assert _loose_path(repo, oid).is_file()


def test_corrupt_index_is_ignored_and_loose_copy_is_kept(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    obj = BlobObject(b"index corruption\n")
    oid = repo.store.write(obj)
    _, idx = _pack(repo, obj)
    damaged = bytearray(idx.read_bytes())
    damaged[-1] ^= 1
    idx.write_bytes(damaged)

    result = prune_packed(repo)

    assert result.pruned == 0
    assert result.ignored_packs
    assert _loose_path(repo, oid).is_file()


def test_corrupt_pack_is_ignored_and_loose_copy_is_kept(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    obj = BlobObject(b"pack corruption\n")
    oid = repo.store.write(obj)
    pack, _ = _pack(repo, obj)
    damaged = bytearray(pack.read_bytes())
    damaged[-1] ^= 1
    pack.write_bytes(damaged)

    result = prune_packed(repo)

    assert result.pruned == 0
    assert result.ignored_packs
    assert _loose_path(repo, oid).is_file()


def test_orphan_pack_or_index_never_establishes_trust(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    left = BlobObject(b"orphan pack\n")
    right = BlobObject(b"orphan index\n")
    left_oid = repo.store.write(left)
    right_oid = repo.store.write(right)
    _, left_idx = _pack(repo, left, prefix="left")
    right_pack, _ = _pack(repo, right, prefix="right")
    left_idx.unlink()
    right_pack.with_suffix(".idx").exists()  # document the paired path before removal
    right_pack.unlink()

    result = prune_packed(repo)

    assert result.pruned == 0
    assert len(result.ignored_packs) == 2
    assert _loose_path(repo, left_oid).is_file()
    assert _loose_path(repo, right_oid).is_file()


def test_valid_index_that_belongs_to_other_pack_is_not_trusted(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first = BlobObject(b"first\n")
    second = BlobObject(b"second\n")
    first_oid = repo.store.write(first)
    second_oid = repo.store.write(second)
    first_pack, first_idx = _pack(repo, first, prefix="first")
    _, second_idx = _pack(repo, second, prefix="second")
    shutil.copyfile(second_idx, first_idx)
    second_idx.unlink()
    second_idx.with_suffix(".pack").unlink()

    result = prune_packed(repo)

    assert result.pruned == 0
    assert result.ignored_packs
    assert _loose_path(repo, first_oid).is_file()
    assert _loose_path(repo, second_oid).is_file()
    assert first_pack.is_file()


def test_invalid_loose_copy_is_never_deleted(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    obj = BlobObject(b"canonical\n")
    oid = repo.store.write(obj)
    _pack(repo, obj)
    _loose_path(repo, oid).write_bytes(zlib.compress(b"blob 6\x00broken"))

    result = prune_packed(repo)

    assert result.pruned == 0
    assert result.skipped_loose == (oid,)
    assert _loose_path(repo, oid).is_file()


def test_one_bad_pack_does_not_block_an_independent_verified_copy(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    obj = BlobObject(b"redundant\n")
    oid = repo.store.write(obj)
    _pack(repo, obj, prefix="good")
    bad_pack, _ = _pack(repo, obj, prefix="bad")
    data = bytearray(bad_pack.read_bytes())
    data[-1] ^= 1
    bad_pack.write_bytes(data)

    result = prune_packed(repo)

    assert result.pruned == 1
    assert result.oids == (oid,)
    assert result.ignored_packs
    assert repo.store.read(oid).data == b"redundant\n"


def test_cli_dry_run_and_verbose_output(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path)
    capsys.readouterr()
    obj = BlobObject(b"cli\n")
    oid = repo.store.write(obj)
    _pack(repo, obj)
    monkeypatch.chdir(repo.worktree)

    assert run_prune_packed(["--dry-run", "--verbose"]) == 0

    captured = capsys.readouterr()
    assert f"would prune {oid}" in captured.out
    assert _loose_path(repo, oid).is_file()
