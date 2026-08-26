"""Phase 117 tests: staged and failure-safe pack/index publication."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import pygit.pack as pack_module
from pygit.objects import BlobObject
from pygit.pack import PackReader, PackWriter


def _writer(payload: bytes = b"phase 117\n") -> tuple[PackWriter, str]:
    blob = BlobObject(payload)
    oid = blob.hash()
    return PackWriter([(oid, blob)]), oid


def _temp_files(directory: Path) -> list[Path]:
    return sorted(directory.glob(".tmp-*"))


def test_written_pair_is_readable_and_contains_no_temp_files(tmp_path: Path) -> None:
    writer, oid = _writer()
    pack_path, idx_path = writer.write_pack_and_idx(tmp_path, "pack")

    assert pack_path.is_file()
    assert idx_path.is_file()
    assert _temp_files(tmp_path) == []

    restored = PackReader(idx_path).read_object(oid)
    assert isinstance(restored, BlobObject)
    assert restored.data == b"phase 117\n"


def test_complete_existing_pair_is_idempotent_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer, _oid = _writer()
    pack_path, idx_path = writer.write_pack_and_idx(tmp_path, "pack")
    pack_before = pack_path.read_bytes()
    idx_before = idx_path.read_bytes()

    def unexpected_temp(*args, **kwargs):
        raise AssertionError("matching immutable pair should not create temporary files")

    monkeypatch.setattr(pack_module.tempfile, "NamedTemporaryFile", unexpected_temp)
    assert writer.write_pack_and_idx(tmp_path, "pack") == (pack_path, idx_path)
    assert pack_path.read_bytes() == pack_before
    assert idx_path.read_bytes() == idx_before


def test_fsync_failure_publishes_neither_file_and_cleans_temps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer, _oid = _writer(b"fsync failure\n")

    def fail_fsync(fd: int) -> None:
        raise OSError("simulated pack fsync failure")

    monkeypatch.setattr(pack_module.os, "fsync", fail_fsync)
    with pytest.raises(OSError, match="simulated pack fsync failure"):
        writer.write_pack_and_idx(tmp_path, "pack")

    assert list(tmp_path.glob("*.pack")) == []
    assert list(tmp_path.glob("*.idx")) == []
    assert _temp_files(tmp_path) == []


def test_index_publish_failure_leaves_safe_pack_only_orphan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer, _oid = _writer(b"rename failure\n")
    real_replace = pack_module.os.replace

    def fail_index_replace(src, dst) -> None:
        destination = Path(dst)
        if destination.suffix == ".idx":
            raise OSError("simulated index publication failure")
        real_replace(src, dst)

    monkeypatch.setattr(pack_module.os, "replace", fail_index_replace)
    with pytest.raises(OSError, match="simulated index publication failure"):
        writer.write_pack_and_idx(tmp_path, "pack")

    packs = list(tmp_path.glob("*.pack"))
    assert len(packs) == 1
    assert list(tmp_path.glob("*.idx")) == []
    assert _temp_files(tmp_path) == []


def test_failed_writer_does_not_delete_pack_used_by_concurrent_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer, oid = _writer(b"concurrent publication\n")
    real_replace = pack_module.os.replace

    def concurrent_index_then_fail(src, dst) -> None:
        destination = Path(dst)
        if destination.suffix == ".idx":
            # Model another identical writer winning the index publication
            # race before this writer reports its own rename failure.
            shutil.copyfile(src, destination)
            raise OSError("simulated losing index publisher")
        real_replace(src, dst)

    monkeypatch.setattr(pack_module.os, "replace", concurrent_index_then_fail)
    with pytest.raises(OSError, match="simulated losing index publisher"):
        writer.write_pack_and_idx(tmp_path, "pack")

    pack_path = next(tmp_path.glob("*.pack"))
    idx_path = next(tmp_path.glob("*.idx"))
    assert pack_path.is_file()
    assert idx_path.is_file()
    assert _temp_files(tmp_path) == []

    restored = PackReader(idx_path).read_object(oid)
    assert isinstance(restored, BlobObject)
    assert restored.data == b"concurrent publication\n"


def test_matching_orphan_pack_is_completed_without_rewriting_pack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer, oid = _writer(b"orphan pack\n")
    staging = tmp_path / "staging"
    final = tmp_path / "final"
    final.mkdir()
    staged_pack, staged_idx = writer.write_pack_and_idx(staging, "pack")
    final_pack = final / staged_pack.name
    final_idx = final / staged_idx.name
    shutil.copyfile(staged_pack, final_pack)

    real_replace = pack_module.os.replace
    destinations: list[Path] = []

    def record_replace(src, dst) -> None:
        destinations.append(Path(dst))
        real_replace(src, dst)

    monkeypatch.setattr(pack_module.os, "replace", record_replace)
    assert writer.write_pack_and_idx(final, "pack") == (final_pack, final_idx)

    assert destinations == [final_idx]
    assert _temp_files(final) == []
    restored = PackReader(final_idx).read_object(oid)
    assert isinstance(restored, BlobObject)
    assert restored.data == b"orphan pack\n"


def test_matching_orphan_index_is_repaired_by_publishing_pack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer, oid = _writer(b"orphan index\n")
    staging = tmp_path / "staging"
    final = tmp_path / "final"
    final.mkdir()
    staged_pack, staged_idx = writer.write_pack_and_idx(staging, "pack")
    final_pack = final / staged_pack.name
    final_idx = final / staged_idx.name
    shutil.copyfile(staged_idx, final_idx)

    real_replace = pack_module.os.replace
    destinations: list[Path] = []

    def record_replace(src, dst) -> None:
        destinations.append(Path(dst))
        real_replace(src, dst)

    monkeypatch.setattr(pack_module.os, "replace", record_replace)
    assert writer.write_pack_and_idx(final, "pack") == (final_pack, final_idx)

    assert destinations == [final_pack]
    assert _temp_files(final) == []
    restored = PackReader(final_idx).read_object(oid)
    assert isinstance(restored, BlobObject)
    assert restored.data == b"orphan index\n"


def test_existing_target_collision_fails_before_staging(tmp_path: Path) -> None:
    writer, _oid = _writer(b"collision\n")
    staging = tmp_path / "staging"
    final = tmp_path / "final"
    final.mkdir()
    staged_pack, _staged_idx = writer.write_pack_and_idx(staging, "pack")
    collision = final / staged_pack.name
    collision.write_bytes(b"unrelated bytes")

    with pytest.raises(RuntimeError, match="pack target collision"):
        writer.write_pack_and_idx(final, "pack")

    assert collision.read_bytes() == b"unrelated bytes"
    assert list(final.glob("*.idx")) == []
    assert _temp_files(final) == []
