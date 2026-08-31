from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from pygit.loose_object_map import (
    lookup_local_sha256,
    lookup_native_sha1,
    publish_staged_loose_object_map,
    read_loose_object_maps,
)
from pygit.objects.blob import BlobObject
from pygit.protocol_v2_packfile_uri_stage import StagedPackfileUriImport
from pygit.repo import Repository


def _staged_blob(repo: Repository, payload: bytes, native: str | None = None):
    blob = BlobObject(payload)
    local = repo.store.write(blob)
    canonical = blob._build_store_bytes()
    real_native = hashlib.sha1(canonical).hexdigest()
    native_oid = native or real_native
    return StagedPackfileUriImport({native_oid: local}, (local,)), local, real_native


def test_consistent_lmap_generations_publish_under_one_repository_namespace(tmp_path: Path):
    repo = Repository.init(str(tmp_path / "repo"))
    first, first_local, first_native = _staged_blob(repo, b"phase341-first\n")
    second, second_local, second_native = _staged_blob(repo, b"phase341-second\n")

    first_map = publish_staged_loose_object_map(repo, first)
    second_map = publish_staged_loose_object_map(repo, second)

    assert first_map.path != second_map.path
    assert len(read_loose_object_maps(repo)) == 2
    assert lookup_local_sha256(repo, first_native) == first_local
    assert lookup_native_sha1(repo, first_local) == first_native
    assert lookup_local_sha256(repo, second_native) == second_local
    assert lookup_native_sha1(repo, second_local) == second_native
    assert not (repo.pygit_dir / "objects" / "object-map" / "publish.lock").exists()


def test_new_generation_cannot_remap_existing_native_sha1(tmp_path: Path):
    repo = Repository.init(str(tmp_path / "repo"))
    first, _, first_native = _staged_blob(repo, b"phase341-native-original\n")
    publish_staged_loose_object_map(repo, first)
    before = tuple((repo.pygit_dir / "objects" / "object-map").glob("map-*.map"))

    conflicting, _, _ = _staged_blob(
        repo,
        b"phase341-native-conflict\n",
        native=first_native,
    )
    with pytest.raises(ValueError, match="existing native SHA-1 mapping"):
        publish_staged_loose_object_map(repo, conflicting)

    after = tuple((repo.pygit_dir / "objects" / "object-map").glob("map-*.map"))
    assert {path.name for path in after} == {path.name for path in before}
    assert not (repo.pygit_dir / "objects" / "object-map" / "publish.lock").exists()


def test_new_generation_cannot_alias_existing_local_sha256(tmp_path: Path):
    repo = Repository.init(str(tmp_path / "repo"))
    first, first_local, _ = _staged_blob(repo, b"phase341-local-original\n")
    publish_staged_loose_object_map(repo, first)

    different_native = "f" * 40
    conflicting = StagedPackfileUriImport(
        {different_native: first_local},
        (first_local,),
    )
    with pytest.raises(ValueError, match="existing local SHA-256 mapping"):
        publish_staged_loose_object_map(repo, conflicting)

    assert len(read_loose_object_maps(repo)) == 1
    assert not (repo.pygit_dir / "objects" / "object-map" / "publish.lock").exists()


def test_existing_writer_lock_is_never_stolen_or_deleted(tmp_path: Path):
    repo = Repository.init(str(tmp_path / "repo"))
    staged, _, _ = _staged_blob(repo, b"phase341-lock-contention\n")
    directory = repo.pygit_dir / "objects" / "object-map"
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / "publish.lock"
    lock.write_text("other-writer\n", encoding="ascii")

    with pytest.raises(RuntimeError, match="publication lock is already held"):
        publish_staged_loose_object_map(repo, staged)

    assert lock.read_text(encoding="ascii") == "other-writer\n"
    assert tuple(directory.glob("map-*.map")) == ()


def test_idempotent_republication_still_rechecks_repository_consistency(tmp_path: Path):
    repo = Repository.init(str(tmp_path / "repo"))
    staged, local, native = _staged_blob(repo, b"phase341-idempotent\n")

    first = publish_staged_loose_object_map(repo, staged)
    second = publish_staged_loose_object_map(repo, staged)

    assert first == second
    assert lookup_local_sha256(repo, native) == local
    assert len(read_loose_object_maps(repo)) == 1
    assert not (repo.pygit_dir / "objects" / "object-map" / "publish.lock").exists()
