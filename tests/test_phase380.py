from __future__ import annotations

import os
from pathlib import Path

import pytest

import pygit.durable_object_store as durable_store
from pygit.objects import BlobObject
from pygit.repo import Repository


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def test_pinned_directory_fence_rejects_replaced_path(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("POSIX directory descriptor semantics")

    directory = tmp_path / "dir"
    directory.mkdir()
    pinned = durable_store._open_pinned_directory(directory)
    assert pinned is not None
    moved = tmp_path / "dir-old"
    try:
        directory.rename(moved)
        directory.mkdir()

        assert durable_store._fence_pinned_directory(pinned, directory) is False
        assert os.fstat(pinned.fd).st_ino == pinned.inode
        assert os.stat(directory, follow_symlinks=False).st_ino != pinned.inode
    finally:
        durable_store._close_pinned_directory(pinned)


def test_directory_pins_are_non_inheritable_and_fd_fsynced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name == "nt":
        pytest.skip("POSIX directory descriptor semantics")

    repo = _repo(tmp_path)
    fanout = repo.store.root / "aa"
    fanout.mkdir()
    fanout_pin, root_pin = durable_store._pin_loose_object_directories(
        fanout,
        repo.store.root,
    )
    assert fanout_pin is not None
    assert root_pin is not None
    assert os.get_inheritable(fanout_pin.fd) is False
    assert os.get_inheritable(root_pin.fd) is False

    real_fsync_retry = durable_store._fsync_retry
    seen: list[int] = []

    def record(fd: int) -> None:
        seen.append(fd)
        real_fsync_retry(fd)

    monkeypatch.setattr(durable_store, "_fsync_retry", record)
    try:
        assert durable_store._fence_pinned_directory(fanout_pin, fanout) is True
        assert durable_store._fence_pinned_directory(root_pin, repo.store.root) is True
        assert fanout_pin.fd in seen
        assert root_pin.fd in seen
    finally:
        durable_store._close_loose_object_directories(fanout_pin, root_pin)


def test_new_publication_retries_after_fanout_directory_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name == "nt":
        pytest.skip("POSIX directory descriptor semantics")

    repo = _repo(tmp_path)
    blob = BlobObject(b"phase380-new-publication-directory-race")
    oid = durable_store.hashlib.new(
        durable_store.HASH_ALGO,
        blob._build_store_bytes(),
    ).hexdigest()
    obj_path = repo.store._path_for(oid)
    fanout = obj_path.parent
    moved = repo.store.root / f"{fanout.name}-old"

    real_fsync_directory = durable_store.fsync_directory
    real_replace = durable_store.os.replace
    injected = False
    publication_replaces = 0

    def count_replace(src, dst) -> None:
        nonlocal publication_replaces
        publication_replaces += 1
        real_replace(src, dst)

    def replace_fanout_once(path: Path) -> None:
        nonlocal injected
        current = Path(path)
        if current == fanout and not injected:
            injected = True
            fanout.rename(moved)
            fanout.mkdir()
        real_fsync_directory(current)

    monkeypatch.setattr(durable_store.os, "replace", count_replace)
    monkeypatch.setattr(durable_store, "fsync_directory", replace_fanout_once)

    assert repo.store.write(blob) == oid

    assert injected is True
    assert publication_replaces == 2
    assert repo.store.read(oid).data == b"phase380-new-publication-directory-race"


def test_existing_certification_repairs_after_fanout_directory_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name == "nt":
        pytest.skip("POSIX directory descriptor semantics")

    repo = _repo(tmp_path)
    blob = BlobObject(b"phase380-existing-directory-race")
    oid = repo.store.write(blob)
    obj_path = repo.store._path_for(oid)
    fanout = obj_path.parent
    moved = repo.store.root / f"{fanout.name}-old"

    real_fsync_directory = durable_store.fsync_directory
    real_replace = durable_store.os.replace
    injected = False
    publication_replaces = 0

    def count_replace(src, dst) -> None:
        nonlocal publication_replaces
        publication_replaces += 1
        real_replace(src, dst)

    def replace_fanout_once(path: Path) -> None:
        nonlocal injected
        current = Path(path)
        if current == fanout and not injected:
            injected = True
            fanout.rename(moved)
            fanout.mkdir()
        real_fsync_directory(current)

    monkeypatch.setattr(durable_store.os, "replace", count_replace)
    monkeypatch.setattr(durable_store, "fsync_directory", replace_fanout_once)

    assert repo.store.write(blob) == oid

    assert injected is True
    assert publication_replaces == 1
    assert repo.store.read(oid).data == b"phase380-existing-directory-race"


def test_objects_root_pin_rejects_namespace_replacement(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("POSIX directory descriptor semantics")

    repo = _repo(tmp_path)
    root = repo.store.root
    pinned = durable_store._open_pinned_directory(root)
    assert pinned is not None
    moved = root.parent / "objects-old"
    try:
        root.rename(moved)
        root.mkdir()

        assert durable_store._fence_pinned_directory(pinned, root) is False
        assert os.stat(root, follow_symlinks=False).st_ino != pinned.inode
    finally:
        durable_store._close_pinned_directory(pinned)
