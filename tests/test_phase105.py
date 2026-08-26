"""Phase 105 tests: multi-pack-index redundant-pack expiration."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from pygit import Repository
from pygit.multi_pack_index import parse_multi_pack_index, verify_multi_pack_index, write_multi_pack_index
from pygit.multi_pack_index_expire import expire_multi_pack_index
from pygit.objects import BlobObject
from pygit.pack import PackWriter


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def _write_pack(repo: Repository, objects: list[BlobObject]):
    pairs = []
    for obj in objects:
        oid = repo.store.write(obj)
        pairs.append((oid, obj))
    return PackWriter(pairs).write_pack_and_idx(repo.pygit_dir / "objects" / "pack")


def _duplicate_pack_pair(repo: Repository):
    first = BlobObject(b"shared-first\n")
    second = BlobObject(b"shared-second\n")
    pack_a, idx_a = _write_pack(repo, [first, second])
    pack_b, idx_b = _write_pack(repo, [second, first])
    assert idx_a != idx_b
    path = write_multi_pack_index(repo.pygit_dir / "objects" / "pack")
    parsed = parse_multi_pack_index(path)
    referenced = {entry.pack_name for entry in parsed.entries}
    assert len(referenced) == 1
    redundant = idx_a if idx_a.name not in referenced else idx_b
    selected = idx_b if redundant == idx_a else idx_a
    return path, selected, redundant


def _run(repo: Repository, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def test_expire_deletes_unreferenced_pack_and_rewrites_midx(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    path, selected, redundant = _duplicate_pack_pair(repo)
    redundant_pack = redundant.with_suffix(".pack")
    assert redundant.is_file() and redundant_pack.is_file()

    result = expire_multi_pack_index(path)

    assert result.expired_packs == (redundant.name,)
    assert result.expired_count == 1
    assert result.kept_packs == (selected.name,)
    assert not redundant.exists()
    assert not redundant_pack.exists()
    verified = verify_multi_pack_index(path)
    assert verified.pack_names == (selected.name,)
    assert verified.object_count == 2


def test_expire_removes_generated_sidecars_with_pack_family(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    path, _, redundant = _duplicate_pack_pair(repo)
    rev = redundant.with_suffix(".rev")
    bitmap = redundant.with_suffix(".bitmap")
    rev.write_bytes(b"reverse-index-sidecar")
    bitmap.write_bytes(b"bitmap-sidecar")

    expire_multi_pack_index(path)

    assert not rev.exists()
    assert not bitmap.exists()


def test_keep_marker_protects_redundant_pack(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    path, selected, redundant = _duplicate_pack_pair(repo)
    keep = redundant.with_suffix(".keep")
    keep.write_text("protected\n", encoding="utf-8")

    before = path.read_bytes()
    result = expire_multi_pack_index(path)

    assert result.expired_packs == ()
    assert set(result.kept_packs) == {selected.name, redundant.name}
    assert path.read_bytes() == before
    assert redundant.exists()
    assert redundant.with_suffix(".pack").exists()
    assert keep.exists()


def test_corrupt_midx_fails_before_any_pack_is_deleted(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    path, selected, redundant = _duplicate_pack_pair(repo)
    data = bytearray(path.read_bytes())
    data[-1] ^= 0x01
    path.write_bytes(data)

    with pytest.raises(ValueError, match="checksum mismatch"):
        expire_multi_pack_index(path)

    for idx in (selected, redundant):
        assert idx.exists()
        assert idx.with_suffix(".pack").exists()


def test_expire_is_noop_when_every_pack_is_referenced(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first = BlobObject(b"one\n")
    second = BlobObject(b"two\n")
    _, idx_a = _write_pack(repo, [first])
    _, idx_b = _write_pack(repo, [second])
    path = write_multi_pack_index(repo.pygit_dir / "objects" / "pack")
    before = path.read_bytes()

    result = expire_multi_pack_index(path)

    assert result.expired_count == 0
    assert set(result.kept_packs) == {idx_a.name, idx_b.name}
    assert path.read_bytes() == before


def test_objects_remain_readable_after_expire(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    path, _, redundant = _duplicate_pack_pair(repo)
    parsed = parse_multi_pack_index(path)
    oids = [entry.oid for entry in parsed.entries]
    for oid in oids:
        repo.store.delete(oid)

    expire_multi_pack_index(path)

    assert not redundant.exists()
    for oid in oids:
        assert repo.store.exists(oid)
        obj = repo.store.read(oid)
        assert isinstance(obj, BlobObject)


def test_installed_cli_expire_and_help(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _, selected, redundant = _duplicate_pack_pair(repo)

    result = _run(repo, "multi-pack-index", "expire")
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""
    assert selected.exists()
    assert not redundant.exists()

    verified = _run(repo, "multi-pack-index", "verify")
    assert verified.returncode == 0, verified.stderr

    help_result = _run(repo, "multi-pack-index", "--help")
    assert help_result.returncode == 0, help_result.stderr
    assert "expire" in help_result.stdout
