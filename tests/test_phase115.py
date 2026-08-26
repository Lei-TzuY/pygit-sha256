"""Phase 115 tests: atomic and repair-safe loose-object writes."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import pygit.store as store_module
from pygit.objects import BlobObject
from pygit.store import ObjectStore


def _store(tmp_path: Path) -> ObjectStore:
    return ObjectStore(tmp_path / "objects")


def test_valid_existing_loose_object_is_idempotent_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    blob = BlobObject(b"already here\n")
    oid = store.write(blob)
    before = store._path_for(oid).read_bytes()

    def unexpected_temp(*args, **kwargs):
        raise AssertionError("valid existing object should not create a temporary file")

    monkeypatch.setattr(store_module.tempfile, "mkstemp", unexpected_temp)
    assert store.write(blob) == oid
    assert store._path_for(oid).read_bytes() == before


def test_write_repairs_corrupt_existing_loose_object(tmp_path: Path) -> None:
    store = _store(tmp_path)
    blob = BlobObject(b"repair me\n")
    oid = store.write(blob)
    target = store._path_for(oid)
    target.write_bytes(b"not a zlib object")

    assert store.write(blob) == oid
    repaired = store.read(oid)
    assert isinstance(repaired, BlobObject)
    assert repaired.data == b"repair me\n"
    assert not list(target.parent.glob(f".tmp-{oid}-*"))


def test_fsync_failure_does_not_publish_partial_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    blob = BlobObject(b"fsync failure\n")
    oid = blob.sha
    target = store._path_for(oid)

    def fail_fsync(fd: int) -> None:
        raise OSError("simulated fsync failure")

    monkeypatch.setattr(store_module.os, "fsync", fail_fsync)
    with pytest.raises(OSError, match="simulated fsync failure"):
        store.write(blob)

    assert not target.exists()
    assert target.parent.is_dir()
    assert not list(target.parent.glob(f".tmp-{oid}-*"))


def test_replace_failure_cleans_temp_and_preserves_existing_corruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    blob = BlobObject(b"replace failure\n")
    oid = blob.sha
    target = store._path_for(oid)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"old corrupt bytes")

    def fail_replace(src, dst) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(store_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        store.write(blob)

    assert target.read_bytes() == b"old corrupt bytes"
    assert not list(target.parent.glob(f".tmp-{oid}-*"))


def test_concurrent_same_object_writers_leave_one_valid_object(tmp_path: Path) -> None:
    store = _store(tmp_path)
    payload = b"same content from many writers\n"
    blob = BlobObject(payload)
    oid = blob.sha

    with ThreadPoolExecutor(max_workers=12) as pool:
        written = list(pool.map(lambda _index: store.write(BlobObject(payload)), range(48)))

    assert set(written) == {oid}
    restored = store.read(oid)
    assert isinstance(restored, BlobObject)
    assert restored.data == payload
    assert not list(store._path_for(oid).parent.glob(f".tmp-{oid}-*"))


def test_all_shas_ignores_atomic_temp_and_junk_files(tmp_path: Path) -> None:
    store = _store(tmp_path)
    oid = store.write(BlobObject(b"enumerate me\n"))
    directory = store._path_for(oid).parent

    (directory / f".tmp-{oid}-interrupted").write_bytes(b"temporary")
    (directory / ("g" * 62)).write_bytes(b"not hex")
    (directory / ("a" * 61)).write_bytes(b"too short")

    assert store.all_shas() == [oid]
