from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from pathlib import Path

import pytest

import pygit.durable_owned_lock as durable
from pygit.objects import BlobObject
from pygit.objects.base import HASH_ALGO
from pygit.repo import Repository


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


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
def test_loose_object_replace_is_followed_by_parent_directory_fsync(
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

    assert kinds == ["file", "dir"]
    assert repo.store.read(oid).data == b"phase370-directory-fence"


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
def test_loose_object_directory_fsync_failure_propagates_after_complete_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    real_fsync = durable.os.fsync
    error = OSError("injected loose-object directory durability failure")
    calls = 0

    def fail_directory(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise error
        real_fsync(fd)

    monkeypatch.setattr(durable.os, "fsync", fail_directory)
    blob = BlobObject(b"phase370-post-replace-failure")

    with pytest.raises(OSError) as excinfo:
        repo.store.write(blob)

    assert excinfo.value is error
    # The atomic replace already happened. The complete object may be visible,
    # but the caller did not receive success because the namespace fence failed.
    envelope = blob._build_store_bytes()
    oid = hashlib.new(HASH_ALGO, envelope).hexdigest()
    assert repo.store.read(oid).data == b"phase370-post-replace-failure"
    assert not list((repo.store.root / oid[:2]).glob(f".tmp-{oid}-*"))


def test_existing_valid_loose_object_fast_path_needs_no_new_fsync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    blob = BlobObject(b"phase370-existing")
    oid = repo.store.write(blob)

    def unexpected(fd: int) -> None:
        raise AssertionError("existing valid loose object should not be republished")

    monkeypatch.setattr(durable.os, "fsync", unexpected)

    assert repo.store.write(blob) == oid
    assert repo.store.read(oid).data == b"phase370-existing"


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
