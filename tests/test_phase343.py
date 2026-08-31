from __future__ import annotations

from pathlib import Path

import pytest

import pygit.protocol_v2_packfile_uri_incremental_fetch as phase343
from pygit.loose_object_map_durable import publish_staged_loose_object_map_durable
from pygit.protocol_v2_packfile_uri_batch import DownloadedPackfileUriBatch
from pygit.protocol_v2_packfile_uri_incremental import PackfileUriIncrementalState
from pygit.protocol_v2_packfile_uri_incremental_fetch import (
    execute_incremental_packfile_uri_fetch_transaction,
)
from pygit.protocol_v2_packfile_uri_refs import PackfileUriRefPublication
from pygit.protocol_v2_packfile_uri_stage import StagedPackfileUriImport
from pygit.refs import ZERO_SHA
from pygit.repo import Repository


EMPTY_BATCH = DownloadedPackfileUriBatch((), {}, 0)


def test_incremental_transaction_uses_phase342_durable_lmap_boundary():
    # Keep the historical monkeypatch name used by Phase336 tests, but bind it
    # to the stronger Phase342 implementation in production.
    assert phase343.publish_staged_loose_object_map is publish_staged_loose_object_map_durable


def test_durability_failure_aborts_before_certification_fetch_head_or_refs(
    tmp_path: Path, monkeypatch
):
    repo = Repository.init(str(tmp_path / "repo"))
    native = "1" * 40
    local = "2" * 64
    staged = StagedPackfileUriImport({native: local}, (local,))
    incremental = PackfileUriIncrementalState((), {}, ())
    publication = PackfileUriRefPublication(native, ZERO_SHA)
    events: list[str] = []

    monkeypatch.setattr(phase343, "_download_optional_packfile_uris", lambda *a, **k: EMPTY_BATCH)
    monkeypatch.setattr(phase343, "stage_packfile_uri_import", lambda *a, **k: staged)

    def fail_durable_map(*args, **kwargs):
        events.append("durability")
        raise OSError("directory fsync failed")

    monkeypatch.setattr(phase343, "publish_staged_loose_object_map", fail_durable_map)
    monkeypatch.setattr(
        phase343,
        "certify_packfile_uri_roots",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not certify")),
    )
    monkeypatch.setattr(
        phase343,
        "_acquire_publication_guard_locks",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not lock refs")),
    )
    monkeypatch.setattr(
        phase343,
        "publish_packfile_uri_refs",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not publish refs")),
    )

    with pytest.raises(OSError, match="directory fsync failed"):
        execute_incremental_packfile_uri_fetch_transaction(
            repo,
            (),
            {native: object()},
            {native: b"commit"},
            {"refs/remotes/origin/main": publication},
            incremental,
            before_ref_publication=lambda certificate: events.append("fetch-head"),
        )

    assert events == ["durability"]
    assert repo.refs.get_remote("origin", "main") is None


def test_known_only_fetch_skips_new_durability_fence(tmp_path: Path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    native = "3" * 40
    local = "4" * 64
    staged = StagedPackfileUriImport({}, ())
    incremental = PackfileUriIncrementalState((native,), {native: local}, ())
    publication = PackfileUriRefPublication(native, ZERO_SHA)
    certificate = object()

    monkeypatch.setattr(phase343, "_download_optional_packfile_uris", lambda *a, **k: EMPTY_BATCH)
    monkeypatch.setattr(phase343, "stage_packfile_uri_import", lambda *a, **k: staged)
    monkeypatch.setattr(
        phase343,
        "publish_staged_loose_object_map",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("known-only fetch must not publish LMAP")),
    )
    monkeypatch.setattr(phase343, "certify_packfile_uri_roots", lambda *a, **k: certificate)
    monkeypatch.setattr(phase343, "_acquire_publication_guard_locks", lambda repo_arg: [])
    monkeypatch.setattr(phase343, "_assert_publication_state_unchanged", lambda *a, **k: None)
    monkeypatch.setattr(phase343, "_release_publication_guard_locks", lambda locks: None)
    monkeypatch.setattr(
        phase343,
        "publish_packfile_uri_refs",
        lambda *a, **k: {"refs/remotes/origin/main": local},
    )

    result = execute_incremental_packfile_uri_fetch_transaction(
        repo,
        (),
        {},
        {native: b"commit"},
        {"refs/remotes/origin/main": publication},
        incremental,
    )

    assert result.object_map is None
    assert result.certificate is certificate
    assert result.published_refs == {"refs/remotes/origin/main": local}
