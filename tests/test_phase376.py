from __future__ import annotations

import os
import zlib
from pathlib import Path

import pytest

import pygit.durable_object_store as durable_store
from pygit.objects import BlobObject
from pygit.repo import Repository


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def _oid(blob: BlobObject) -> str:
    return durable_store.hashlib.new(
        durable_store.HASH_ALGO,
        blob._build_store_bytes(),
    ).hexdigest()


def test_new_publication_retains_pinned_inode_through_directory_fences(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    blob = BlobObject(b"phase376-pinned-publication")
    oid = _oid(blob)
    obj_path = repo.store._path_for(oid)
    real_replace = durable_store.os.replace
    real_fsync_directory = durable_store.fsync_directory
    published_identity: tuple[int, int] | None = None
    checks: list[tuple[int, int]] = []

    def record_replace(src, dst) -> None:
        nonlocal published_identity
        real_replace(src, dst)
        st = os.stat(dst, follow_symlinks=False)
        published_identity = (st.st_dev, st.st_ino)

    def record_fence(path: Path) -> None:
        assert published_identity is not None
        st = os.stat(obj_path, follow_symlinks=False)
        checks.append((st.st_dev, st.st_ino))
        real_fsync_directory(Path(path))

    monkeypatch.setattr(durable_store.os, "replace", record_replace)
    monkeypatch.setattr(durable_store, "fsync_directory", record_fence)

    assert repo.store.write(blob) == oid
    assert checks == [published_identity, published_identity]
    assert repo.store.read(oid).data == b"phase376-pinned-publication"


def test_valid_competing_replacement_is_certified_before_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    blob = BlobObject(b"phase376-valid-race")
    oid = _oid(blob)
    obj_path = repo.store._path_for(oid)
    store_bytes = blob._build_store_bytes()
    replacement = tmp_path / "valid-replacement"
    replacement.write_bytes(zlib.compress(store_bytes))
    real_replace = durable_store.os.replace
    real_fsync_directory = durable_store.fsync_directory
    replace_calls = 0
    injected = False

    def count_replace(src, dst) -> None:
        nonlocal replace_calls
        replace_calls += 1
        real_replace(src, dst)

    def replace_during_fanout_fence(path: Path) -> None:
        nonlocal injected
        current = Path(path)
        if current == obj_path.parent and not injected:
            injected = True
            real_replace(replacement, obj_path)
        real_fsync_directory(current)

    monkeypatch.setattr(durable_store.os, "replace", count_replace)
    monkeypatch.setattr(durable_store, "fsync_directory", replace_during_fanout_fence)

    assert repo.store.write(blob) == oid
    assert injected is True
    assert replace_calls == 1
    assert repo.store.read(oid).data == b"phase376-valid-race"


def test_corrupt_competing_replacement_forces_atomic_republish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    blob = BlobObject(b"phase376-corrupt-race")
    oid = _oid(blob)
    obj_path = repo.store._path_for(oid)
    replacement = tmp_path / "corrupt-replacement"
    replacement.write_bytes(zlib.compress(b"blob 3\x00bad"))
    real_replace = durable_store.os.replace
    real_fsync_directory = durable_store.fsync_directory
    publication_replaces = 0
    injected = False

    def count_replace(src, dst) -> None:
        nonlocal publication_replaces
        publication_replaces += 1
        real_replace(src, dst)

    def replace_once_during_fanout_fence(path: Path) -> None:
        nonlocal injected
        current = Path(path)
        if current == obj_path.parent and not injected:
            injected = True
            real_replace(replacement, obj_path)
        real_fsync_directory(current)

    monkeypatch.setattr(durable_store.os, "replace", count_replace)
    monkeypatch.setattr(durable_store, "fsync_directory", replace_once_during_fanout_fence)

    assert repo.store.write(blob) == oid
    assert injected is True
    assert publication_replaces == 2
    assert repo.store.read(oid).data == b"phase376-corrupt-race"


def test_post_replace_directory_failure_still_propagates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    blob = BlobObject(b"phase376-directory-failure")
    oid = _oid(blob)
    obj_path = repo.store._path_for(oid)
    failure = OSError("injected fanout durability failure")

    monkeypatch.setattr(
        durable_store,
        "fsync_directory",
        lambda path: (_ for _ in ()).throw(failure),
    )

    with pytest.raises(OSError) as excinfo:
        repo.store.write(blob)

    assert excinfo.value is failure
    assert obj_path.is_file()
    assert repo.store.read(oid).data == b"phase376-directory-failure"


def test_new_publication_descriptor_is_non_inheritable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    blob = BlobObject(b"phase376-non-inheritable")
    seen: list[bool] = []
    real_fstat = durable_store.os.fstat

    def record_fstat(fd: int):
        seen.append(os.get_inheritable(fd))
        return real_fstat(fd)

    monkeypatch.setattr(durable_store.os, "fstat", record_fstat)

    repo.store.write(blob)
    assert seen
    assert False in seen
