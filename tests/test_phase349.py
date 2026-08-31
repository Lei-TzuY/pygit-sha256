from __future__ import annotations

import os
from pathlib import Path

import pytest

import pygit.protocol_v2_packfile_uri_transaction as phase349
from pygit.repo import Repository


def _guard_paths(repo: Repository) -> tuple[Path, ...]:
    return phase349._publication_guard_lock_paths(repo)


def test_publication_guard_descriptors_are_non_inheritable(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    lock = repo.pygit_dir / "probe.lock"

    fd = phase349._open_publication_guard_lock(lock)
    try:
        assert os.get_inheritable(fd) is False
    finally:
        os.close(fd)
        lock.unlink()


def test_fsync_failure_cleans_current_and_previously_acquired_guards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    real_fsync = phase349.os.fsync
    calls = 0

    def fail_second_fsync(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected publication guard fsync failure")
        real_fsync(fd)

    monkeypatch.setattr(phase349.os, "fsync", fail_second_fsync)

    with pytest.raises(OSError, match="injected publication guard fsync failure"):
        phase349._acquire_publication_guard_locks(repo)

    # Phase365 routes rollback through the durable owned-lock primitive. On
    # POSIX, removing the previously acquired guard therefore adds one parent
    # directory fsync after the second initialization fsync fails. Windows keeps
    # the established no-directory-fsync boundary.
    assert calls == (2 if os.name == "nt" else 3)
    assert all(not path.exists() for path in _guard_paths(repo))


def test_descriptor_hardening_failure_cleans_new_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = Repository.init(str(tmp_path / "repo"))

    def fail_set_inheritable(fd: int, inheritable: bool) -> None:
        raise OSError("injected descriptor hardening failure")

    monkeypatch.setattr(phase349.os, "set_inheritable", fail_set_inheritable)

    with pytest.raises(OSError, match="descriptor hardening failure"):
        phase349._acquire_publication_guard_locks(repo)

    assert all(not path.exists() for path in _guard_paths(repo))


def test_foreign_existing_guard_is_never_removed(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    first = sorted(_guard_paths(repo))[0]
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_bytes(b"foreign writer\n")

    with pytest.raises(RuntimeError, match="lock file already exists"):
        phase349._acquire_publication_guard_locks(repo)

    assert first.read_bytes() == b"foreign writer\n"


def test_successful_guard_set_keeps_canonical_markers_until_release(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))

    acquired = phase349._acquire_publication_guard_locks(repo)
    try:
        assert tuple(acquired) == tuple(sorted(_guard_paths(repo)))
        for path in acquired:
            assert path.read_bytes() == b"packfile-uri publication guard\n"
    finally:
        phase349._release_publication_guard_locks(acquired)

    assert all(not path.exists() for path in _guard_paths(repo))
