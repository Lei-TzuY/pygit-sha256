from __future__ import annotations

from pathlib import Path

import pytest

import pygit.protocol_v2_packfile_uri_incremental_fetch as incremental
import pygit.protocol_v2_packfile_uri_refs as refpub
import pygit.protocol_v2_packfile_uri_transaction as transaction
from pygit.protocol_v2_packfile_uri_batch import DownloadedPackfileUriBatch
from pygit.protocol_v2_packfile_uri_connectivity import PackfileUriRootCertificate
from pygit.protocol_v2_packfile_uri_incremental import PackfileUriIncrementalState
from pygit.protocol_v2_packfile_uri_refs import PackfileUriRefPublication
from pygit.protocol_v2_packfile_uri_stage import StagedPackfileUriImport
from pygit.refs import ZERO_SHA
from pygit.repo import Repository


EMPTY_BATCH = DownloadedPackfileUriBatch((), {}, 0)
NATIVE = "9" * 40
TARGET = "refs/remotes/origin/main"


def _commit(repo: Repository) -> str:
    path = repo.worktree / "phase359.txt"
    path.write_text("durable ownership integration\n", encoding="utf-8")
    repo.add(["phase359.txt"])
    return repo.commit("phase359 durable ownership integration")


def _run_known_transaction(
    repo: Repository,
    monkeypatch: pytest.MonkeyPatch,
    tip: str,
):
    staged = StagedPackfileUriImport({}, ())
    state = PackfileUriIncrementalState((NATIVE,), {NATIVE: tip}, ())
    certificate = PackfileUriRootCertificate({NATIVE: tip}, {NATIVE: b"commit"})

    monkeypatch.setattr(
        incremental,
        "_download_optional_packfile_uris",
        lambda *args, **kwargs: EMPTY_BATCH,
    )
    monkeypatch.setattr(
        incremental,
        "stage_packfile_uri_import",
        lambda *args, **kwargs: staged,
    )
    monkeypatch.setattr(
        incremental,
        "certify_packfile_uri_roots",
        lambda *args, **kwargs: certificate,
    )

    return incremental.execute_incremental_packfile_uri_fetch_transaction(
        repo,
        (),
        {},
        {NATIVE: b"commit"},
        {TARGET: PackfileUriRefPublication(NATIVE, ZERO_SHA)},
        state,
        before_ref_publication=lambda seen: None,
    )


def test_durable_ref_fences_run_inside_both_outer_guard_classes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    tip = _commit(repo)
    events: list[tuple[str, str]] = []

    assert incremental.publish_packfile_uri_refs is refpub.publish_packfile_uri_refs

    def assert_guarded(kind: str, path: Path) -> None:
        state_lock = incremental._fetch_head_state_guard_path(repo.pygit_dir)
        assert state_lock in incremental._FETCH_HEAD_STATE_GUARD_OWNERSHIP
        repo_locks = transaction._publication_guard_lock_paths(repo)
        assert repo_locks
        assert all(lock in transaction._PUBLICATION_GUARD_OWNERSHIP for lock in repo_locks)
        events.append((kind, path.relative_to(repo.pygit_dir).as_posix() or "."))

    monkeypatch.setattr(refpub, "_fsync_file", lambda path: assert_guarded("file", path))
    monkeypatch.setattr(
        refpub,
        "_fsync_directory",
        lambda path: assert_guarded("directory", path),
    )

    result = _run_known_transaction(repo, monkeypatch, tip)

    assert result.published_refs == {TARGET: tip}
    assert any(kind == "file" for kind, _ in events)
    assert any(kind == "directory" for kind, _ in events)
    assert not incremental._FETCH_HEAD_STATE_GUARD_OWNERSHIP
    assert not transaction._PUBLICATION_GUARD_OWNERSHIP
    assert not incremental._fetch_head_state_guard_path(repo.pygit_dir).exists()
    assert all(not path.exists() for path in transaction._publication_guard_lock_paths(repo))


def test_ref_durability_failure_unwinds_both_outer_ownership_layers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    tip = _commit(repo)
    calls = 0

    def fail_file_fsync(path: Path) -> None:
        nonlocal calls
        calls += 1
        state_lock = incremental._fetch_head_state_guard_path(repo.pygit_dir)
        assert state_lock in incremental._FETCH_HEAD_STATE_GUARD_OWNERSHIP
        assert all(
            lock in transaction._PUBLICATION_GUARD_OWNERSHIP
            for lock in transaction._publication_guard_lock_paths(repo)
        )
        raise OSError("injected Phase359 ref durability failure")

    monkeypatch.setattr(refpub, "_fsync_file", fail_file_fsync)

    with pytest.raises(OSError, match="Phase359 ref durability failure"):
        _run_known_transaction(repo, monkeypatch, tip)

    assert calls == 1
    # Phase350 documents success-after-durability rather than rollback-after-
    # visibility: the ref transaction may already be visible when fsync fails.
    assert repo.refs.get_remote("origin", "main") == tip
    assert not incremental._FETCH_HEAD_STATE_GUARD_OWNERSHIP
    assert not transaction._PUBLICATION_GUARD_OWNERSHIP
    assert not incremental._fetch_head_state_guard_path(repo.pygit_dir).exists()
    assert all(not path.exists() for path in transaction._publication_guard_lock_paths(repo))
