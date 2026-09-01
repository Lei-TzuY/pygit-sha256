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


def test_retry_after_objects_root_fsync_failure_recovers_via_existing_object(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    blob = BlobObject(b"phase373-root-retry")
    calls: list[Path] = []
    real_fsync_directory = durable_store.fsync_directory
    failure = OSError("injected root durability failure")
    failed = False

    def fail_first_root(path: Path) -> None:
        nonlocal failed
        current = Path(path)
        calls.append(current)
        if current == repo.store.root and not failed:
            failed = True
            raise failure
        real_fsync_directory(current)

    monkeypatch.setattr(durable_store, "fsync_directory", fail_first_root)

    with pytest.raises(OSError) as excinfo:
        repo.store.write(blob)
    assert excinfo.value is failure

    oid = durable_store.hashlib.new(
        durable_store.HASH_ALGO,
        blob._build_store_bytes(),
    ).hexdigest()
    obj_path = repo.store._path_for(oid)
    assert repo.store.read(oid).data == b"phase373-root-retry"

    def unexpected_replace(src, dst) -> None:
        raise AssertionError("retry should certify the visible object rather than republish it")

    monkeypatch.setattr(durable_store.os, "replace", unexpected_replace)

    assert repo.store.write(blob) == oid
    assert calls == [
        obj_path.parent,
        repo.store.root,
        obj_path.parent,
        repo.store.root,
    ]


def test_existing_object_file_fsync_failure_is_not_reported_as_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    blob = BlobObject(b"phase373-existing-file-fsync")
    oid = repo.store.write(blob)
    error = OSError("injected existing-object durability failure")

    monkeypatch.setattr(
        durable_store,
        "_fsync_retry",
        lambda fd: (_ for _ in ()).throw(error),
    )
    monkeypatch.setattr(
        durable_store.os,
        "replace",
        lambda src, dst: (_ for _ in ()).throw(
            AssertionError("failed certification must not republish before surfacing fsync failure")
        ),
    )

    with pytest.raises(OSError) as excinfo:
        repo.store.write(blob)

    assert excinfo.value is error
    assert repo.store.read(oid).data == b"phase373-existing-file-fsync"


def test_interrupted_existing_object_read_is_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    blob = BlobObject(b"phase373-read-eintr")
    oid = repo.store.write(blob)
    real_read = durable_store.os.read
    calls = 0

    def interrupt_once(fd: int, size: int) -> bytes:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise InterruptedError("injected read EINTR")
        return real_read(fd, size)

    monkeypatch.setattr(durable_store.os, "read", interrupt_once)

    assert repo.store.write(blob) == oid
    assert calls >= 2


def test_path_replacement_during_certification_falls_back_to_atomic_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    blob = BlobObject(b"phase373-path-race")
    oid = repo.store.write(blob)
    obj_path = repo.store._path_for(oid)
    replacement = obj_path.parent / ".phase373-race-replacement"
    replacement.write_bytes(zlib.compress(b"blob 3\x00bad"))
    real_fsync_directory = durable_store.fsync_directory
    injected = False

    def replace_after_descriptor_fsync(path: Path) -> None:
        nonlocal injected
        current = Path(path)
        if not injected and current == obj_path.parent:
            injected = True
            os.replace(replacement, obj_path)
        real_fsync_directory(current)

    monkeypatch.setattr(durable_store, "fsync_directory", replace_after_descriptor_fsync)

    assert repo.store.write(blob) == oid
    assert injected is True
    assert repo.store.read(oid).data == b"phase373-path-race"
    assert not replacement.exists()


@pytest.mark.skipif(os.name == "nt", reason="symlink replacement contract is POSIX-specific")
def test_existing_symlink_is_not_trusted_as_loose_object_fast_path(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    blob = BlobObject(b"phase373-symlink")
    store_bytes = blob._build_store_bytes()
    oid = durable_store.hashlib.new(durable_store.HASH_ALGO, store_bytes).hexdigest()
    obj_path = repo.store._path_for(oid)
    obj_path.parent.mkdir(parents=True, exist_ok=True)

    external = tmp_path / "external-loose-object"
    external.write_bytes(zlib.compress(store_bytes))
    obj_path.symlink_to(external)

    assert repo.store.write(blob) == oid
    assert obj_path.is_file()
    assert not obj_path.is_symlink()
    assert external.exists()
    assert repo.store.read(oid).data == b"phase373-symlink"


def test_corrupt_existing_path_is_repaired_by_normal_publication(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    blob = BlobObject(b"phase373-repair-corrupt")
    oid = repo.store.write(blob)
    obj_path = repo.store._path_for(oid)
    obj_path.write_bytes(zlib.compress(b"blob 7\x00corrupt"))

    assert repo.store.write(blob) == oid
    assert repo.store.read(oid).data == b"phase373-repair-corrupt"
