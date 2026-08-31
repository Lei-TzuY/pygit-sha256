from __future__ import annotations

import os
from pathlib import Path

import pytest

import pygit.fetch_head_durable as durable
import pygit.protocol_v2_packfile_uri_incremental_fetch as incremental
from pygit.fetch_head_durable import write_fetch_head_durable


def test_incremental_fetch_binds_durable_fetch_head_writer():
    assert incremental.write_fetch_head is write_fetch_head_durable


def test_durable_fetch_head_replaces_stale_file(tmp_path: Path):
    pygit_dir = tmp_path / ".pygit"
    pygit_dir.mkdir()
    path = pygit_dir / "FETCH_HEAD"
    path.write_text("stale\n", encoding="utf-8")
    oid = "a" * 64

    write_fetch_head_durable(
        pygit_dir,
        {"refs/heads/main": oid},
        source="https://example.test/repo.git",
        mergeable=("refs/heads/main",),
    )

    assert path.read_text(encoding="utf-8") == (
        f"{oid}\t\tbranch 'main' of https://example.test/repo.git\n"
    )
    assert not list(pygit_dir.glob("FETCH_HEAD.*.lock"))


def test_empty_durable_fetch_head_atomically_truncates(tmp_path: Path):
    pygit_dir = tmp_path / ".pygit"
    pygit_dir.mkdir()
    path = pygit_dir / "FETCH_HEAD"
    path.write_text("stale\n", encoding="utf-8")

    write_fetch_head_durable(pygit_dir, {}, source="https://example.test/repo.git")

    assert path.read_bytes() == b""


def test_replace_failure_preserves_old_fetch_head_and_cleans_temp(tmp_path: Path, monkeypatch):
    pygit_dir = tmp_path / ".pygit"
    pygit_dir.mkdir()
    path = pygit_dir / "FETCH_HEAD"
    path.write_text("old\n", encoding="utf-8")

    def fail_replace(*args, **kwargs):
        raise OSError("replace failed")

    monkeypatch.setattr(durable.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        write_fetch_head_durable(
            pygit_dir,
            {"refs/heads/main": "b" * 64},
            source="remote",
        )

    assert path.read_text(encoding="utf-8") == "old\n"
    assert not list(pygit_dir.glob("FETCH_HEAD.*.lock"))


def test_directory_fsync_failure_propagates_after_atomic_visibility(tmp_path: Path, monkeypatch):
    pygit_dir = tmp_path / ".pygit"
    oid = "c" * 64

    def fail_directory_fsync(path: Path):
        raise OSError("directory fsync failed")

    monkeypatch.setattr(durable, "_fsync_directory", fail_directory_fsync)
    with pytest.raises(OSError, match="directory fsync failed"):
        write_fetch_head_durable(
            pygit_dir,
            {"refs/heads/main": oid},
            source="remote",
        )

    assert (pygit_dir / "FETCH_HEAD").read_text(encoding="utf-8").startswith(oid)
    assert not list(pygit_dir.glob("FETCH_HEAD.*.lock"))


def test_rejects_non_sha256_native_oid_before_publication(tmp_path: Path):
    pygit_dir = tmp_path / ".pygit"
    with pytest.raises(ValueError, match="64-hex SHA-256"):
        write_fetch_head_durable(
            pygit_dir,
            {"refs/heads/main": "d" * 40},
            source="remote",
        )
    assert not (pygit_dir / "FETCH_HEAD").exists()


def test_directory_fsync_is_explicit_noop_on_windows(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(durable.os, "name", "nt")
    durable._fsync_directory(tmp_path)
