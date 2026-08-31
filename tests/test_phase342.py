from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import pygit.loose_object_map_durable as durable
from pygit.loose_object_map import lookup_local_sha256, read_loose_object_maps
from pygit.objects.blob import BlobObject
from pygit.protocol_v2_packfile_uri_stage import StagedPackfileUriImport
from pygit.repo import Repository


def _staged_blob(repo: Repository, payload: bytes) -> tuple[StagedPackfileUriImport, str, str]:
    blob = BlobObject(payload)
    local = repo.store.write(blob)
    native = hashlib.sha1(blob._build_store_bytes()).hexdigest()
    return StagedPackfileUriImport({native: local}, (local,)), native, local


def test_durable_publication_fences_map_directory_then_objects_parent(tmp_path: Path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    staged, _, _ = _staged_blob(repo, b"phase342-order\n")
    events: list[Path] = []

    monkeypatch.setattr(durable, "_fsync_directory", lambda path: events.append(Path(path)))
    published = durable.publish_staged_loose_object_map_durable(repo, staged)

    directory = repo.pygit_dir / "objects" / "object-map"
    assert published.path.parent == directory
    assert events == [directory, directory.parent]


def test_durable_publication_preserves_real_git_lmap_lookup(tmp_path: Path):
    repo = Repository.init(str(tmp_path / "repo"))
    staged, native, local = _staged_blob(repo, b"phase342-real-map\n")

    published = durable.publish_staged_loose_object_map_durable(repo, staged)

    assert published.path.exists()
    assert published.path.name == f"map-{published.checksum}.map"
    assert lookup_local_sha256(repo, native) == local
    assert len(read_loose_object_maps(repo)) == 1
    assert not (published.path.parent / "publish.lock").exists()


def test_durability_failure_is_propagated_after_safe_immutable_publication(tmp_path: Path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    staged, native, local = _staged_blob(repo, b"phase342-fsync-failure\n")
    calls = 0

    def fail_first(_path: Path) -> None:
        nonlocal calls
        calls += 1
        raise OSError("directory fsync failed")

    monkeypatch.setattr(durable, "_fsync_directory", fail_first)
    with pytest.raises(OSError, match="directory fsync failed"):
        durable.publish_staged_loose_object_map_durable(repo, staged)

    assert calls == 1
    # Phase341 publication happens before the durability fence. Its immutable,
    # content-authenticated generation is safe to retain and retry.
    assert lookup_local_sha256(repo, native) == local


def test_retry_after_durability_failure_is_idempotent(tmp_path: Path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    staged, native, local = _staged_blob(repo, b"phase342-retry\n")
    real_fsync = durable._fsync_directory
    failed = False

    def fail_once(path: Path) -> None:
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("simulated crash boundary")
        real_fsync(path)

    monkeypatch.setattr(durable, "_fsync_directory", fail_once)
    with pytest.raises(OSError, match="simulated crash boundary"):
        durable.publish_staged_loose_object_map_durable(repo, staged)

    monkeypatch.setattr(durable, "_fsync_directory", real_fsync)
    published = durable.publish_staged_loose_object_map_durable(repo, staged)

    assert published.path.exists()
    assert lookup_local_sha256(repo, native) == local
    assert len(read_loose_object_maps(repo)) == 1


def test_directory_fsync_closes_descriptor_when_fsync_raises(tmp_path: Path, monkeypatch):
    if durable.os.name == "nt":
        pytest.skip("directory-fd durability fence is POSIX-specific")

    directory = tmp_path / "directory"
    directory.mkdir()
    real_open = durable.os.open
    real_close = durable.os.close
    opened: list[int] = []
    closed: list[int] = []

    def tracked_open(path, flags):
        fd = real_open(path, flags)
        opened.append(fd)
        return fd

    def tracked_close(fd):
        closed.append(fd)
        return real_close(fd)

    monkeypatch.setattr(durable.os, "open", tracked_open)
    monkeypatch.setattr(durable.os, "close", tracked_close)
    monkeypatch.setattr(durable.os, "fsync", lambda _fd: (_ for _ in ()).throw(OSError("boom")))

    with pytest.raises(OSError, match="boom"):
        durable._fsync_directory(directory)

    assert len(opened) == 1
    assert closed == opened


def test_windows_directory_fsync_boundary_is_explicit_noop(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(durable.os, "name", "nt")
    monkeypatch.setattr(durable.os, "open", lambda *_args, **_kwargs: pytest.fail("os.open called"))

    durable._fsync_directory(tmp_path)
