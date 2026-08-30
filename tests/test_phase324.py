from pathlib import Path

import pytest

from pygit import Repository
from pygit.protocol_v2_packfile_uri_batch import DownloadedPackfileUriBatch
from pygit.protocol_v2_packfile_uri_connectivity import PackfileUriRootCertificate
from pygit.protocol_v2_packfile_uri_refs import PackfileUriRefPublication
from pygit.protocol_v2_packfile_uri_stage import StagedPackfileUriImport
from pygit.refs import ZERO_SHA
import pygit.protocol_v2_packfile_uri_transaction as transaction


NATIVE = "a" * 40
LOCAL = "b" * 64


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def _plan():
    return (
        {NATIVE: b"commit"},
        {"refs/heads/fetched": PackfileUriRefPublication(NATIVE, ZERO_SHA)},
    )


def test_runs_verified_boundaries_in_order_and_returns_all_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    expected_roots, publications = _plan()
    batch = DownloadedPackfileUriBatch((), {}, 17)
    staged = StagedPackfileUriImport({NATIVE: LOCAL}, (LOCAL,))
    certificate = PackfileUriRootCertificate({NATIVE: LOCAL}, {NATIVE: b"commit"})
    calls = []

    def fake_download(descriptors, **kwargs):
        calls.append(("download", tuple(descriptors), kwargs))
        return batch

    def fake_stage(store, inline_objects, received_batch):
        calls.append(("stage", store, inline_objects, received_batch))
        assert received_batch is batch
        return staged

    def fake_certify(store, received_staged, roots):
        calls.append(("certify", store, received_staged, roots))
        assert received_staged is staged
        return certificate

    def fake_publish(received_repo, received_certificate, refs, *, message):
        calls.append(("publish", received_repo, received_certificate, refs, message))
        assert received_certificate is certificate
        return {"refs/heads/fetched": LOCAL}

    monkeypatch.setattr(transaction, "download_packfile_uris", fake_download)
    monkeypatch.setattr(transaction, "stage_packfile_uri_import", fake_stage)
    monkeypatch.setattr(transaction, "certify_packfile_uri_roots", fake_certify)
    monkeypatch.setattr(transaction, "publish_packfile_uri_refs", fake_publish)

    result = transaction.execute_packfile_uri_fetch_transaction(
        repo,
        descriptors=("descriptor-a", "descriptor-b"),
        inline_objects={},
        expected_roots=expected_roots,
        publications=publications,
        message="fetch: phase324",
        timeout=9,
        max_pack_bytes=101,
        max_total_bytes=202,
        max_packs=3,
        opener="opener",
    )

    assert [call[0] for call in calls] == ["download", "stage", "certify", "publish"]
    assert calls[0][1] == ("descriptor-a", "descriptor-b")
    assert calls[0][2] == {
        "timeout": 9,
        "max_pack_bytes": 101,
        "max_total_bytes": 202,
        "max_packs": 3,
        "opener": "opener",
    }
    assert result.batch is batch
    assert result.staged is staged
    assert result.certificate is certificate
    assert result.published_refs == {"refs/heads/fetched": LOCAL}


@pytest.mark.parametrize("failure_stage", ["download", "stage", "certify"])
def test_failure_before_publication_never_calls_ref_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_stage: str
) -> None:
    repo = _repo(tmp_path)
    expected_roots, publications = _plan()
    batch = DownloadedPackfileUriBatch((), {}, 0)
    staged = StagedPackfileUriImport({NATIVE: LOCAL}, (LOCAL,))
    published = False

    def fail_or_download(*args, **kwargs):
        if failure_stage == "download":
            raise RuntimeError("download failed")
        return batch

    def fail_or_stage(*args, **kwargs):
        if failure_stage == "stage":
            raise RuntimeError("stage failed")
        return staged

    def fail_or_certify(*args, **kwargs):
        if failure_stage == "certify":
            raise RuntimeError("certify failed")
        return PackfileUriRootCertificate({NATIVE: LOCAL}, {NATIVE: b"commit"})

    def should_not_publish(*args, **kwargs):
        nonlocal published
        published = True
        raise AssertionError("ref publication must be the final step")

    monkeypatch.setattr(transaction, "download_packfile_uris", fail_or_download)
    monkeypatch.setattr(transaction, "stage_packfile_uri_import", fail_or_stage)
    monkeypatch.setattr(transaction, "certify_packfile_uri_roots", fail_or_certify)
    monkeypatch.setattr(transaction, "publish_packfile_uri_refs", should_not_publish)

    with pytest.raises(RuntimeError, match=f"{failure_stage} failed"):
        transaction.execute_packfile_uri_fetch_transaction(
            repo, (), {}, expected_roots, publications
        )

    assert published is False
    assert repo.refs.resolve("refs/heads/fetched") is None


def test_publication_failure_is_not_reported_as_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    expected_roots, publications = _plan()
    batch = DownloadedPackfileUriBatch((), {}, 0)
    staged = StagedPackfileUriImport({NATIVE: LOCAL}, (LOCAL,))
    certificate = PackfileUriRootCertificate({NATIVE: LOCAL}, {NATIVE: b"commit"})

    monkeypatch.setattr(transaction, "download_packfile_uris", lambda *a, **k: batch)
    monkeypatch.setattr(transaction, "stage_packfile_uri_import", lambda *a, **k: staged)
    monkeypatch.setattr(transaction, "certify_packfile_uri_roots", lambda *a, **k: certificate)

    def fail_publish(*args, **kwargs):
        raise RuntimeError("stale expected old id")

    monkeypatch.setattr(transaction, "publish_packfile_uri_refs", fail_publish)

    with pytest.raises(RuntimeError, match="stale expected old id"):
        transaction.execute_packfile_uri_fetch_transaction(
            repo, (), {}, expected_roots, publications
        )


def test_rejects_inconsistent_publication_plan_before_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    called = False

    def fake_download(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("preflight failure must happen before network I/O")

    monkeypatch.setattr(transaction, "download_packfile_uris", fake_download)

    with pytest.raises(ValueError, match="declared in expected_roots"):
        transaction.execute_packfile_uri_fetch_transaction(
            repo,
            (),
            {},
            {"c" * 40: b"commit"},
            {"refs/heads/fetched": PackfileUriRefPublication(NATIVE, ZERO_SHA)},
        )

    assert called is False


def test_rejects_empty_or_non_ref_publication_plan_before_network(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    with pytest.raises(ValueError, match="at least one expected root"):
        transaction.execute_packfile_uri_fetch_transaction(repo, (), {}, {}, {})

    with pytest.raises(ValueError, match="full refs"):
        transaction.execute_packfile_uri_fetch_transaction(
            repo,
            (),
            {},
            {NATIVE: b"commit"},
            {"HEAD": PackfileUriRefPublication(NATIVE, ZERO_SHA)},
        )
