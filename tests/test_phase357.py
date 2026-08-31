from __future__ import annotations

import os
from pathlib import Path

import pytest

import pygit.protocol_v2_packfile_uri_incremental_fetch as incremental
import pygit.protocol_v2_packfile_uri_transaction as transaction
from pygit.protocol_v2_packfile_uri_batch import DownloadedPackfileUriBatch
from pygit.protocol_v2_packfile_uri_incremental import PackfileUriIncrementalState
from pygit.protocol_v2_packfile_uri_refs import PackfileUriRefPublication
from pygit.protocol_v2_packfile_uri_stage import StagedPackfileUriImport
from pygit.refs import ZERO_SHA
from pygit.repo import Repository


EMPTY_BATCH = DownloadedPackfileUriBatch((), {}, 0)
STATE_REPLACEMENT = b"replacement FETCH_HEAD state owner\n"
REPO_REPLACEMENT = b"replacement repository publication owner\n"


def test_complete_incremental_transaction_preserves_both_replacement_guard_classes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    native = "1" * 40
    local = "2" * 64
    staged = StagedPackfileUriImport({}, ())
    incremental_state = PackfileUriIncrementalState((native,), {native: local}, ())
    publication = PackfileUriRefPublication(native, ZERO_SHA)
    certificate = object()

    monkeypatch.setattr(incremental, "_download_optional_packfile_uris", lambda *a, **k: EMPTY_BATCH)
    monkeypatch.setattr(incremental, "stage_packfile_uri_import", lambda *a, **k: staged)
    monkeypatch.setattr(incremental, "certify_packfile_uri_roots", lambda *a, **k: certificate)
    monkeypatch.setattr(
        incremental,
        "publish_packfile_uri_refs",
        lambda *a, **k: {"refs/remotes/origin/main": local},
    )

    state_lock = incremental._fetch_head_state_guard_path(repo.pygit_dir)
    repo_lock = sorted(transaction._publication_guard_lock_paths(repo))[0]
    retained_fds: list[int] = []

    def replace_both_guard_pathnames(seen) -> None:
        assert seen is certificate

        state_owner = incremental._FETCH_HEAD_STATE_GUARD_OWNERSHIP[state_lock]
        repo_owner = transaction._PUBLICATION_GUARD_OWNERSHIP[repo_lock]
        retained_fds.extend((state_owner.fd, repo_owner.fd))

        state_lock.unlink()
        state_lock.write_bytes(STATE_REPLACEMENT)
        repo_lock.unlink()
        repo_lock.write_bytes(REPO_REPLACEMENT)

        state_stat = os.stat(state_lock, follow_symlinks=False)
        repo_stat = os.stat(repo_lock, follow_symlinks=False)
        assert (state_stat.st_dev, state_stat.st_ino) != (
            state_owner.device,
            state_owner.inode,
        )
        assert (repo_stat.st_dev, repo_stat.st_ino) != (
            repo_owner.device,
            repo_owner.inode,
        )

    result = incremental.execute_incremental_packfile_uri_fetch_transaction(
        repo,
        (),
        {},
        {native: b"commit"},
        {"refs/remotes/origin/main": publication},
        incremental_state,
        before_ref_publication=replace_both_guard_pathnames,
    )

    assert result.published_refs == {"refs/remotes/origin/main": local}
    assert state_lock.read_bytes() == STATE_REPLACEMENT
    assert repo_lock.read_bytes() == REPO_REPLACEMENT
    assert state_lock not in incremental._FETCH_HEAD_STATE_GUARD_OWNERSHIP
    assert repo_lock not in transaction._PUBLICATION_GUARD_OWNERSHIP

    for fd in retained_fds:
        with pytest.raises(OSError):
            os.fstat(fd)

    for lock in transaction._publication_guard_lock_paths(repo):
        if lock != repo_lock:
            assert not lock.exists()


def test_integrated_guard_release_is_independent_between_guard_classes(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))

    state_lock = incremental._acquire_fetch_head_state_guard(repo.pygit_dir)
    repo_locks = transaction._acquire_publication_guard_locks(repo)
    try:
        assert state_lock in incremental._FETCH_HEAD_STATE_GUARD_OWNERSHIP
        assert all(lock in transaction._PUBLICATION_GUARD_OWNERSHIP for lock in repo_locks)

        incremental._release_fetch_head_state_guard(state_lock)
        assert not state_lock.exists()
        assert all(lock.exists() for lock in repo_locks)
        assert all(lock in transaction._PUBLICATION_GUARD_OWNERSHIP for lock in repo_locks)

        transaction._release_publication_guard_locks(repo_locks)
        assert all(not lock.exists() for lock in repo_locks)
    finally:
        incremental._release_fetch_head_state_guard(state_lock)
        transaction._release_publication_guard_locks(repo_locks)

    assert not incremental._FETCH_HEAD_STATE_GUARD_OWNERSHIP
    assert not transaction._PUBLICATION_GUARD_OWNERSHIP
