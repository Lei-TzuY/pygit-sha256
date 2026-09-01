from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from pathlib import Path

import pytest

import pygit.durable_object_store as durable_store
import pygit.durable_owned_lock as durable
from pygit.objects import BlobObject
from pygit.objects.base import HASH_ALGO
from pygit.repo import Repository


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def _oid(blob: BlobObject) -> str:
    return hashlib.new(HASH_ALGO, blob._build_store_bytes()).hexdigest()


def test_loose_object_file_fsync_retries_eintr_before_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    real_fsync = durable.os.fsync
    calls = 0

    def interrupt_first(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise InterruptedError("injected loose-object EINTR")
        real_fsync(fd)

    monkeypatch.setattr(durable.os, "fsync", interrupt_first)

    oid = repo.store.write(BlobObject(b"phase370-eintr"))

    assert calls >= 2
    assert len(oid) == 64
    assert repo.store.read(oid).data == b"phase370-eintr"


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory-fsync contract")
def test_new_fanout_publication_fsyncs_file_fanout_and_objects_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    real_fsync = durable.os.fsync
    kinds: list[str] = []

    def record_kind(fd: int) -> None:
        mode = os.fstat(fd).st_mode
        kinds.append("dir" if stat.S_ISDIR(mode) else "file")
        real_fsync(fd)

    monkeypatch.setattr(durable.os, "fsync", record_kind)

    oid = repo.store.write(BlobObject(b"phase370-directory-fence"))

    # Phase370 supplied one object-file fsync plus pathname-opened fanout/root
    # fences. Phase380 retains those two compatibility fences and adds one fsync
    # on each pinned directory descriptor, so all five durability operations are
    # intentional and ordered after the object contents become durable.
    assert kinds == ["file", "dir", "dir", "dir", "dir"]
    assert repo.store.read(oid).data == b"phase370-directory-fence"


def test_preexisting_fanout_still_fences_objects_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    blob = BlobObject(b"phase370-existing-fanout")
    oid = _oid(blob)
    fanout = repo.store.root / oid[:2]
    fanout.mkdir(parents=True, exist_ok=True)
    fenced: list[Path] = []

    def record(path: Path) -> None:
        fenced.append(Path(path))

    monkeypatch.setattr(durable_store, "fsync_directory", record)

    assert repo.store.write(blob) == oid
    assert fenced == [fanout, repo.store.root]


def test_loose_object_file_fsync_failure_does_not_publish_object(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    error = OSError("injected loose-object file durability failure")

    def fail(fd: int) -> None:
        raise error

    monkeypatch.setattr(durable.os, "fsync", fail)

    with pytest.raises(OSError) as excinfo:
        repo.store.write(BlobObject(b"phase370-pre-replace-failure"))

    assert excinfo.value is error
    fanouts = [path for path in repo.store.root.iterdir() if path.is_dir() and len(path.name) == 2]
    assert all(not any(path.iterdir()) for path in fanouts)


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory-fsync contract")
def test_loose_object_fanout_fsync_failure_propagates_after_complete_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    real_fsync = durable.os.fsync
    error = OSError("injected loose-object fanout durability failure")
    calls = 0

    def fail_fanout(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise error
        real_fsync(fd)

    monkeypatch.setattr(durable.os, "fsync", fail_fanout)
    blob = BlobObject(b"phase370-post-replace-failure")

    with pytest.raises(OSError) as excinfo:
        repo.store.write(blob)

    assert excinfo.value is error
    oid = _oid(blob)
    assert repo.store.read(oid).data == b"phase370-post-replace-failure"
    assert not list((repo.store.root / oid[:2]).glob(f".tmp-{oid}-*"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory-fsync contract")
def test_objects_root_fsync_failure_does_not_report_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    real_fsync = durable.os.fsync
    error = OSError("injected objects-root durability failure")
    calls = 0

    def fail_root(fd: int) -> None:
        nonlocal calls
        calls += 1
        # Phase380 inserts the retained-fanout descriptor fence as call 3. The
        # mature pathname-opened objects-root fence is therefore call 4.
        if calls == 4:
            raise error
        real_fsync(fd)

    monkeypatch.setattr(durable.os, "fsync", fail_root)
    blob = BlobObject(b"phase370-root-fence-failure")

    with pytest.raises(OSError) as excinfo:
        repo.store.write(blob)

    assert excinfo.value is error
    oid = _oid(blob)
    assert repo.store.read(oid).data == b"phase370-root-fence-failure"


def test_existing_valid_loose_object_is_recertified_without_republication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    blob = BlobObject(b"phase370-existing")
    oid = repo.store.write(blob)
    obj_path = repo.store._path_for(oid)
    file_fsyncs = 0
    pinned_directory_fsyncs = 0
    fenced: list[Path] = []

    def record_inode_fsync(fd: int) -> None:
        nonlocal file_fsyncs, pinned_directory_fsyncs
        mode = os.fstat(fd).st_mode
        if stat.S_ISDIR(mode):
            pinned_directory_fsyncs += 1
        else:
            file_fsyncs += 1

    def record_directory(path: Path) -> None:
        fenced.append(Path(path))

    def unexpected_replace(src, dst) -> None:
        raise AssertionError("existing valid loose object should not be republished")

    monkeypatch.setattr(durable_store, "_fsync_retry", record_inode_fsync)
    monkeypatch.setattr(durable_store, "fsync_directory", record_directory)
    monkeypatch.setattr(durable_store.os, "replace", unexpected_replace)

    assert repo.store.write(blob) == oid
    assert repo.store.read(oid).data == b"phase370-existing"
    assert file_fsyncs == 1
    assert pinned_directory_fsyncs == 2
    assert fenced == [obj_path.parent, repo.store.root]


def test_durable_writer_preserves_native_git_sha256_blob_identity(tmp_path: Path) -> None:
    native = tmp_path / "native"
    subprocess.run(
        ["git", "init", "--object-format=sha256", str(native)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    payload = b"phase370-native-sha256\n"
    native_oid = subprocess.run(
        ["git", "-C", str(native), "hash-object", "--stdin"],
        input=payload,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout.decode("ascii").strip()

    repo = _repo(tmp_path)
    pygit_oid = repo.store.write(BlobObject(payload))

    assert len(native_oid) == 64
    assert pygit_oid == native_oid
