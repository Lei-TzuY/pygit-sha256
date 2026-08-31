from __future__ import annotations

import os
from pathlib import Path

import pytest

import pygit.protocol_v2_packfile_uri_transaction as phase353
from pygit.repo import Repository


FOREIGN = b"foreign replacement writer\n"


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def test_acquired_guards_retain_non_inheritable_identity_fds(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    acquired = phase353._acquire_publication_guard_locks(repo)
    ownerships = [phase353._PUBLICATION_GUARD_OWNERSHIP[path] for path in acquired]
    try:
        assert ownerships
        for path, ownership in zip(acquired, ownerships):
            assert os.get_inheritable(ownership.fd) is False
            fd_stat = os.fstat(ownership.fd)
            path_stat = os.stat(path, follow_symlinks=False)
            assert (fd_stat.st_dev, fd_stat.st_ino) == (
                ownership.device,
                ownership.inode,
            )
            assert (path_stat.st_dev, path_stat.st_ino) == (
                ownership.device,
                ownership.inode,
            )
    finally:
        phase353._release_publication_guard_locks(acquired)

    for ownership in ownerships:
        with pytest.raises(OSError):
            os.fstat(ownership.fd)


def test_release_preserves_recreated_foreign_lock(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    acquired = phase353._acquire_publication_guard_locks(repo)
    replaced = acquired[0]
    old = phase353._PUBLICATION_GUARD_OWNERSHIP[replaced]

    replaced.unlink()
    replaced.write_bytes(FOREIGN)
    replacement_stat = os.stat(replaced, follow_symlinks=False)
    assert (replacement_stat.st_dev, replacement_stat.st_ino) != (
        old.device,
        old.inode,
    )

    phase353._release_publication_guard_locks(acquired)

    assert replaced.read_bytes() == FOREIGN
    assert replaced not in phase353._PUBLICATION_GUARD_OWNERSHIP
    with pytest.raises(OSError):
        os.fstat(old.fd)
    for path in acquired[1:]:
        assert not path.exists()


def test_release_preserves_recreated_lock_even_with_canonical_marker(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    acquired = phase353._acquire_publication_guard_locks(repo)
    replaced = acquired[-1]
    old = phase353._PUBLICATION_GUARD_OWNERSHIP[replaced]

    replaced.unlink()
    replaced.write_bytes(phase353._PUBLICATION_GUARD_MARKER)
    replacement_stat = os.stat(replaced, follow_symlinks=False)
    assert (replacement_stat.st_dev, replacement_stat.st_ino) != (
        old.device,
        old.inode,
    )

    phase353._release_publication_guard_locks(acquired)

    assert replaced.read_bytes() == phase353._PUBLICATION_GUARD_MARKER
    with pytest.raises(OSError):
        os.fstat(old.fd)


def test_release_missing_owned_path_closes_descriptor(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    acquired = phase353._acquire_publication_guard_locks(repo)
    missing = acquired[1]
    ownership = phase353._PUBLICATION_GUARD_OWNERSHIP[missing]

    missing.unlink()
    phase353._release_publication_guard_locks(acquired)

    assert not missing.exists()
    assert missing not in phase353._PUBLICATION_GUARD_OWNERSHIP
    with pytest.raises(OSError):
        os.fstat(ownership.fd)


def test_release_without_recorded_ownership_never_unlinks_path(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    lock = repo.pygit_dir / "foreign-only.lock"
    lock.write_bytes(FOREIGN)

    phase353._release_publication_guard_locks([lock])

    assert lock.read_bytes() == FOREIGN


def test_acquire_rollback_closes_retained_descriptors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    real_initializer = phase353._initialize_owned_publication_guard_lock
    calls = 0
    retained_fds: list[int] = []

    def fail_after_first(lock: Path):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected second guard initialization failure")
        ownership = real_initializer(lock)
        retained_fds.append(ownership.fd)
        return ownership

    monkeypatch.setattr(
        phase353,
        "_initialize_owned_publication_guard_lock",
        fail_after_first,
    )

    with pytest.raises(OSError, match="second guard initialization failure"):
        phase353._acquire_publication_guard_locks(repo)

    assert retained_fds
    for fd in retained_fds:
        with pytest.raises(OSError):
            os.fstat(fd)
    assert not phase353._PUBLICATION_GUARD_OWNERSHIP
    assert all(not path.exists() for path in phase353._publication_guard_lock_paths(repo))
