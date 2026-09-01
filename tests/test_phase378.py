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


def _assert_exact_stream(path: Path, expected: bytes) -> None:
    raw = path.read_bytes()
    decoder = zlib.decompressobj()
    output = decoder.decompress(raw)
    assert decoder.eof is True
    assert decoder.unused_data == b""
    assert decoder.unconsumed_tail == b""
    assert output == expected


def test_racing_trailing_garbage_winner_is_rejected_and_republished(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    blob = BlobObject(b"phase378-racing-trailing-garbage")
    expected = blob._build_store_bytes()
    oid = _oid(blob)
    obj_path = repo.store._path_for(oid)
    replacement = tmp_path / "trailing-garbage-winner"
    replacement.write_bytes(zlib.compress(expected) + b"junk-after-zlib-stream")

    real_replace = durable_store.os.replace
    real_fsync_directory = durable_store.fsync_directory
    publication_replaces = 0
    injected = False

    def count_publication_replace(src, dst) -> None:
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

    monkeypatch.setattr(durable_store.os, "replace", count_publication_replace)
    monkeypatch.setattr(durable_store, "fsync_directory", replace_once_during_fanout_fence)

    assert repo.store.write(blob) == oid

    assert injected is True
    assert publication_replaces == 2
    _assert_exact_stream(obj_path, expected)
    assert repo.store.read(oid).data == b"phase378-racing-trailing-garbage"


def test_racing_concatenated_stream_winner_is_rejected_and_republished(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    blob = BlobObject(b"phase378-racing-concatenated-stream")
    expected = blob._build_store_bytes()
    oid = _oid(blob)
    obj_path = repo.store._path_for(oid)
    replacement = tmp_path / "concatenated-winner"
    replacement.write_bytes(zlib.compress(expected) + zlib.compress(b"second-stream"))

    real_replace = durable_store.os.replace
    real_fsync_directory = durable_store.fsync_directory
    publication_replaces = 0
    injected = False

    def count_publication_replace(src, dst) -> None:
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

    monkeypatch.setattr(durable_store.os, "replace", count_publication_replace)
    monkeypatch.setattr(durable_store, "fsync_directory", replace_once_during_fanout_fence)

    assert repo.store.write(blob) == oid

    assert injected is True
    assert publication_replaces == 2
    _assert_exact_stream(obj_path, expected)


def test_racing_exact_stream_winner_is_still_accepted_after_strict_certification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    blob = BlobObject(b"phase378-racing-exact-stream")
    expected = blob._build_store_bytes()
    oid = _oid(blob)
    obj_path = repo.store._path_for(oid)
    replacement = tmp_path / "exact-winner"
    replacement.write_bytes(zlib.compress(expected))

    real_replace = durable_store.os.replace
    real_fsync_directory = durable_store.fsync_directory
    publication_replaces = 0
    injected = False

    def count_publication_replace(src, dst) -> None:
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

    monkeypatch.setattr(durable_store.os, "replace", count_publication_replace)
    monkeypatch.setattr(durable_store, "fsync_directory", replace_once_during_fanout_fence)

    assert repo.store.write(blob) == oid

    assert injected is True
    assert publication_replaces == 1
    _assert_exact_stream(obj_path, expected)


def test_new_publication_and_existing_certification_share_exact_envelope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    blob = BlobObject(b"phase378-same-envelope")
    expected = blob._build_store_bytes()
    oid = _oid(blob)
    seen: list[bytes] = []
    real_certify = durable_store._certify_existing_loose_object

    def record_certify(path: Path, expected_store_bytes: bytes, objects_root: Path) -> bool:
        seen.append(expected_store_bytes)
        return real_certify(path, expected_store_bytes, objects_root)

    monkeypatch.setattr(durable_store, "_certify_existing_loose_object", record_certify)

    assert repo.store.write(blob) == oid
    assert repo.store.write(blob) == oid

    assert seen
    assert all(item == expected for item in seen)
    assert len(oid) == 64
