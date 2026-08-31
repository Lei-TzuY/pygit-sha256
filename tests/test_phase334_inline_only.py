from pathlib import Path

import pytest

import pygit.protocol_v2_packfile_uri_incremental_fetch as phase334
from pygit.protocol_v2_packfile_uri_incremental import PackfileUriIncrementalState
from pygit.protocol_v2_packfile_uri_refs import PackfileUriRefPublication
from pygit.protocol_v2_packfile_uri_stage import StagedPackfileUriImport
from pygit.refs import ZERO_SHA
from pygit.repo import Repository


def test_optional_packfile_uri_batch_accepts_inline_only_and_keeps_resource_validation():
    batch = phase334._download_optional_packfile_uris(
        (),
        timeout=30,
        max_pack_bytes=1024,
        max_total_bytes=2048,
        max_packs=2,
        opener=None,
    )

    assert batch.downloads == ()
    assert batch.objects == {}
    assert batch.total_bytes == 0

    with pytest.raises(ValueError, match="timeout must be a positive integer"):
        phase334._download_optional_packfile_uris(
            (),
            timeout=0,
            max_pack_bytes=1024,
            max_total_bytes=2048,
            max_packs=2,
            opener=None,
        )


def test_optional_packfile_uri_batch_does_not_swallow_nonempty_download_errors(monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError("descriptor verification failed")

    monkeypatch.setattr(phase334, "download_packfile_uris", fail)

    with pytest.raises(ValueError, match="descriptor verification failed"):
        phase334._download_optional_packfile_uris(
            (object(),),
            timeout=30,
            max_pack_bytes=1024,
            max_total_bytes=2048,
            max_packs=2,
            opener=None,
        )


def test_incremental_transaction_stages_inline_objects_without_uri_descriptors(
    tmp_path: Path,
    monkeypatch,
):
    repo = Repository.init(str(tmp_path / "repo"))
    native = "1" * 40
    local = "2" * 64
    incremental = PackfileUriIncrementalState((), {}, ())
    publication = PackfileUriRefPublication(native, ZERO_SHA)
    staged = StagedPackfileUriImport({native: local}, (local,))
    object_map = object()
    certificate = object()
    captured = {}

    def fake_stage(store, inline_objects, external_batch, **kwargs):
        captured["inline"] = inline_objects
        captured["batch"] = external_batch
        captured["known"] = kwargs["known_native_to_local"]
        return staged

    monkeypatch.setattr(phase334, "stage_packfile_uri_import", fake_stage)
    monkeypatch.setattr(
        phase334,
        "publish_staged_loose_object_map",
        lambda repo_arg, staged_arg: object_map,
    )
    monkeypatch.setattr(phase334, "certify_packfile_uri_roots", lambda *a, **k: certificate)
    monkeypatch.setattr(phase334, "_acquire_publication_guard_locks", lambda repo: [])
    monkeypatch.setattr(phase334, "_assert_publication_state_unchanged", lambda *a, **k: None)
    monkeypatch.setattr(phase334, "_release_publication_guard_locks", lambda locks: None)
    monkeypatch.setattr(
        phase334,
        "publish_packfile_uri_refs",
        lambda *a, **k: {"refs/remotes/origin/main": local},
    )

    inline = {native: object()}
    result = phase334.execute_incremental_packfile_uri_fetch_transaction(
        repo,
        (),
        inline,
        {native: b"commit"},
        {"refs/remotes/origin/main": publication},
        incremental,
    )

    assert captured["inline"] is inline
    assert captured["batch"].downloads == ()
    assert captured["batch"].objects == {}
    assert captured["batch"].total_bytes == 0
    assert captured["known"] == {}
    assert result.batch is captured["batch"]
    assert result.staged is staged
    assert result.object_map is object_map
    assert result.certificate is certificate
