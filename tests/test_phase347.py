from __future__ import annotations

from pathlib import Path

import pytest

import pygit.protocol_v2_packfile_uri_incremental_fetch as phase347
from pygit.protocol_v2_packfile_uri_batch import DownloadedPackfileUriBatch
from pygit.protocol_v2_packfile_uri_incremental import PackfileUriIncrementalState
from pygit.protocol_v2_packfile_uri_refs import PackfileUriRefPublication
from pygit.protocol_v2_packfile_uri_stage import StagedPackfileUriImport
from pygit.refs import ZERO_SHA
from pygit.repo import Repository


EMPTY_BATCH = DownloadedPackfileUriBatch((), {}, 0)


def _known_only_fixture(tmp_path: Path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    native = "1" * 40
    local = "2" * 64
    staged = StagedPackfileUriImport({}, ())
    incremental = PackfileUriIncrementalState((native,), {native: local}, ())
    publication = PackfileUriRefPublication(native, ZERO_SHA)
    certificate = object()

    monkeypatch.setattr(phase347, "_download_optional_packfile_uris", lambda *a, **k: EMPTY_BATCH)
    monkeypatch.setattr(phase347, "stage_packfile_uri_import", lambda *a, **k: staged)
    monkeypatch.setattr(phase347, "certify_packfile_uri_roots", lambda *a, **k: certificate)

    return repo, native, local, incremental, publication, certificate


def test_fetch_head_hook_runs_inside_final_publication_guard(tmp_path: Path, monkeypatch):
    repo, native, local, incremental, publication, certificate = _known_only_fixture(
        tmp_path, monkeypatch
    )
    events: list[str] = []
    locks = [repo.pygit_dir / "HEAD.lock"]

    monkeypatch.setattr(
        phase347,
        "_acquire_publication_guard_locks",
        lambda repo_arg: events.append("acquire") or locks,
    )
    monkeypatch.setattr(
        phase347,
        "_assert_publication_state_unchanged",
        lambda *a, **k: events.append("assert-state"),
    )
    monkeypatch.setattr(
        phase347,
        "publish_packfile_uri_refs",
        lambda *a, **k: events.append("refs")
        or {"refs/remotes/origin/main": local},
    )
    monkeypatch.setattr(
        phase347,
        "_release_publication_guard_locks",
        lambda held: events.append("release") or None,
    )

    result = phase347.execute_incremental_packfile_uri_fetch_transaction(
        repo,
        (),
        {},
        {native: b"commit"},
        {"refs/remotes/origin/main": publication},
        incremental,
        before_ref_publication=lambda seen: events.append("fetch-head")
        if seen is certificate
        else (_ for _ in ()).throw(AssertionError("wrong certificate")),
    )

    assert events == ["acquire", "assert-state", "fetch-head", "refs", "release"]
    assert result.published_refs == {"refs/remotes/origin/main": local}


def test_guard_contention_cannot_publish_fetch_head(tmp_path: Path, monkeypatch):
    repo, native, _, incremental, publication, _ = _known_only_fixture(tmp_path, monkeypatch)
    events: list[str] = []

    def fail_guard(repo_arg):
        events.append("acquire")
        raise RuntimeError("publication guard is busy")

    monkeypatch.setattr(phase347, "_acquire_publication_guard_locks", fail_guard)
    monkeypatch.setattr(
        phase347,
        "publish_packfile_uri_refs",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("refs must not publish")),
    )

    with pytest.raises(RuntimeError, match="publication guard is busy"):
        phase347.execute_incremental_packfile_uri_fetch_transaction(
            repo,
            (),
            {},
            {native: b"commit"},
            {"refs/remotes/origin/main": publication},
            incremental,
            before_ref_publication=lambda certificate: events.append("fetch-head"),
        )

    assert events == ["acquire"]


def test_state_change_after_guard_aborts_before_fetch_head(tmp_path: Path, monkeypatch):
    repo, native, _, incremental, publication, _ = _known_only_fixture(tmp_path, monkeypatch)
    events: list[str] = []

    monkeypatch.setattr(
        phase347,
        "_acquire_publication_guard_locks",
        lambda repo_arg: events.append("acquire") or [],
    )

    def fail_state(*args, **kwargs):
        events.append("assert-state")
        raise RuntimeError("repository changed")

    monkeypatch.setattr(phase347, "_assert_publication_state_unchanged", fail_state)
    monkeypatch.setattr(
        phase347,
        "_release_publication_guard_locks",
        lambda held: events.append("release") or None,
    )
    monkeypatch.setattr(
        phase347,
        "publish_packfile_uri_refs",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("refs must not publish")),
    )

    with pytest.raises(RuntimeError, match="repository changed"):
        phase347.execute_incremental_packfile_uri_fetch_transaction(
            repo,
            (),
            {},
            {native: b"commit"},
            {"refs/remotes/origin/main": publication},
            incremental,
            before_ref_publication=lambda certificate: events.append("fetch-head"),
        )

    assert events == ["acquire", "assert-state", "release"]


def test_fetch_head_failure_releases_guard_and_blocks_refs(tmp_path: Path, monkeypatch):
    repo, native, _, incremental, publication, _ = _known_only_fixture(tmp_path, monkeypatch)
    events: list[str] = []

    monkeypatch.setattr(
        phase347,
        "_acquire_publication_guard_locks",
        lambda repo_arg: events.append("acquire") or [],
    )
    monkeypatch.setattr(
        phase347,
        "_assert_publication_state_unchanged",
        lambda *a, **k: events.append("assert-state"),
    )
    monkeypatch.setattr(
        phase347,
        "_release_publication_guard_locks",
        lambda held: events.append("release") or None,
    )
    monkeypatch.setattr(
        phase347,
        "publish_packfile_uri_refs",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("refs must not publish")),
    )

    def fail_fetch_head(certificate):
        events.append("fetch-head")
        raise OSError("FETCH_HEAD durability failure")

    with pytest.raises(OSError, match="FETCH_HEAD durability failure"):
        phase347.execute_incremental_packfile_uri_fetch_transaction(
            repo,
            (),
            {},
            {native: b"commit"},
            {"refs/remotes/origin/main": publication},
            incremental,
            before_ref_publication=fail_fetch_head,
        )

    assert events == ["acquire", "assert-state", "fetch-head", "release"]
