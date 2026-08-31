from __future__ import annotations

import os
from pathlib import Path

import pytest

import pygit.fetch_head_durable as durable
from pygit.fetch_head_durable import write_fetch_head_durable


def test_existing_fetch_head_lock_fails_closed_without_stealing(tmp_path: Path):
    pygit_dir = tmp_path / ".pygit"
    pygit_dir.mkdir()
    live = pygit_dir / "FETCH_HEAD"
    lock = pygit_dir / "FETCH_HEAD.lock"
    live.write_text("old\n", encoding="utf-8")
    lock.write_text("other writer\n", encoding="utf-8")

    with pytest.raises(FileExistsError):
        write_fetch_head_durable(
            pygit_dir,
            {"refs/heads/main": "a" * 64},
            source="remote",
        )

    assert live.read_text(encoding="utf-8") == "old\n"
    assert lock.read_text(encoding="utf-8") == "other writer\n"


def test_success_commits_canonical_lockfile_to_fetch_head(tmp_path: Path, monkeypatch):
    pygit_dir = tmp_path / ".pygit"
    pygit_dir.mkdir()
    calls: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def record_replace(src, dst):
        calls.append((Path(src), Path(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(durable.os, "replace", record_replace)
    write_fetch_head_durable(
        pygit_dir,
        {"refs/heads/main": "b" * 64},
        source="remote",
        mergeable=("refs/heads/main",),
    )

    assert calls == [(pygit_dir / "FETCH_HEAD.lock", pygit_dir / "FETCH_HEAD")]
    assert not (pygit_dir / "FETCH_HEAD.lock").exists()
    assert (pygit_dir / "FETCH_HEAD").read_text(encoding="utf-8").startswith("b" * 64)


def test_owned_lock_is_removed_when_write_fsync_fails(tmp_path: Path, monkeypatch):
    pygit_dir = tmp_path / ".pygit"
    original_fsync = durable.os.fsync
    calls = 0

    def fail_first_fsync(fd: int):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("file fsync failed")
        return original_fsync(fd)

    monkeypatch.setattr(durable.os, "fsync", fail_first_fsync)
    with pytest.raises(OSError, match="file fsync failed"):
        write_fetch_head_durable(
            pygit_dir,
            {"refs/heads/main": "c" * 64},
            source="remote",
        )

    assert not (pygit_dir / "FETCH_HEAD.lock").exists()
    assert not (pygit_dir / "FETCH_HEAD").exists()


def test_replace_failure_rolls_back_only_owned_lock(tmp_path: Path, monkeypatch):
    pygit_dir = tmp_path / ".pygit"
    pygit_dir.mkdir()
    live = pygit_dir / "FETCH_HEAD"
    live.write_text("old\n", encoding="utf-8")

    def fail_replace(src, dst):
        assert Path(src) == pygit_dir / "FETCH_HEAD.lock"
        raise OSError("replace failed")

    monkeypatch.setattr(durable.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        write_fetch_head_durable(
            pygit_dir,
            {"refs/heads/main": "d" * 64},
            source="remote",
        )

    assert live.read_text(encoding="utf-8") == "old\n"
    assert not (pygit_dir / "FETCH_HEAD.lock").exists()


def test_render_validation_happens_before_lock_acquisition(tmp_path: Path):
    pygit_dir = tmp_path / ".pygit"
    pygit_dir.mkdir()
    foreign_lock = pygit_dir / "FETCH_HEAD.lock"
    foreign_lock.write_text("other writer\n", encoding="utf-8")

    with pytest.raises(ValueError, match="64-hex SHA-256"):
        write_fetch_head_durable(
            pygit_dir,
            {"refs/heads/main": "e" * 40},
            source="remote",
        )

    assert foreign_lock.read_text(encoding="utf-8") == "other writer\n"


def test_directory_fsync_failure_occurs_after_lock_commit(tmp_path: Path, monkeypatch):
    pygit_dir = tmp_path / ".pygit"
    oid = "f" * 64

    def fail_directory_fsync(path: Path):
        assert path == pygit_dir
        assert not (pygit_dir / "FETCH_HEAD.lock").exists()
        assert (pygit_dir / "FETCH_HEAD").read_text(encoding="utf-8").startswith(oid)
        raise OSError("directory fsync failed")

    monkeypatch.setattr(durable, "_fsync_directory", fail_directory_fsync)
    with pytest.raises(OSError, match="directory fsync failed"):
        write_fetch_head_durable(
            pygit_dir,
            {"refs/heads/main": oid},
            source="remote",
        )

    assert not (pygit_dir / "FETCH_HEAD.lock").exists()
    assert (pygit_dir / "FETCH_HEAD").read_text(encoding="utf-8").startswith(oid)
