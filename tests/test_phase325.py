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


def _install_successful_prepublication_stages(monkeypatch: pytest.MonkeyPatch):
    batch = DownloadedPackfileUriBatch((), {}, 0)
    staged = StagedPackfileUriImport({NATIVE: LOCAL}, (LOCAL,))
    certificate = PackfileUriRootCertificate({NATIVE: LOCAL}, {NATIVE: b"commit"})
    monkeypatch.setattr(transaction, "download_packfile_uris", lambda *a, **k: batch)
    monkeypatch.setattr(transaction, "stage_packfile_uri_import", lambda *a, **k: staged)
    monkeypatch.setattr(transaction, "certify_packfile_uri_roots", lambda *a, **k: certificate)
    return batch, staged, certificate


@pytest.mark.parametrize(
    "relative_path",
    [
        "HEAD",
        "packed-refs",
        "promisor.json",
        "shallow",
        "refs/heads/fetched",
        "logs/HEAD",
        "logs/refs/heads/fetched",
    ],
)
def test_mutable_state_change_aborts_before_ref_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative_path: str,
) -> None:
    repo = _repo(tmp_path)
    expected_roots, publications = _plan()
    batch = DownloadedPackfileUriBatch((), {}, 0)
    staged = StagedPackfileUriImport({NATIVE: LOCAL}, (LOCAL,))
    published = False

    monkeypatch.setattr(transaction, "download_packfile_uris", lambda *a, **k: batch)
    monkeypatch.setattr(transaction, "stage_packfile_uri_import", lambda *a, **k: staged)

    def mutate_then_certify(*args, **kwargs):
        path = repo.pygit_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"phase325 injected mutation\n")
        return PackfileUriRootCertificate({NATIVE: LOCAL}, {NATIVE: b"commit"})

    def must_not_publish(*args, **kwargs):
        nonlocal published
        published = True
        raise AssertionError("mutable-state guard must run before ref publication")

    monkeypatch.setattr(transaction, "certify_packfile_uri_roots", mutate_then_certify)
    monkeypatch.setattr(transaction, "publish_packfile_uri_refs", must_not_publish)

    with pytest.raises(RuntimeError, match="mutable repository state changed") as exc:
        transaction.execute_packfile_uri_fetch_transaction(
            repo, (), {}, expected_roots, publications
        )

    assert relative_path in str(exc.value)
    assert published is False


def test_mutable_state_guard_detects_network_window_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    expected_roots, publications = _plan()
    batch = DownloadedPackfileUriBatch((), {}, 0)
    staged = StagedPackfileUriImport({NATIVE: LOCAL}, (LOCAL,))
    certificate = PackfileUriRootCertificate({NATIVE: LOCAL}, {NATIVE: b"commit"})

    def racing_download(*args, **kwargs):
        repo.pygit_dir.joinpath("HEAD").write_text(
            "ref: refs/heads/concurrent\n", encoding="utf-8"
        )
        return batch

    monkeypatch.setattr(transaction, "download_packfile_uris", racing_download)
    monkeypatch.setattr(transaction, "stage_packfile_uri_import", lambda *a, **k: staged)
    monkeypatch.setattr(transaction, "certify_packfile_uri_roots", lambda *a, **k: certificate)
    monkeypatch.setattr(
        transaction,
        "publish_packfile_uri_refs",
        lambda *a, **k: pytest.fail("concurrent HEAD change must abort before publication"),
    )

    with pytest.raises(RuntimeError, match="HEAD"):
        transaction.execute_packfile_uri_fetch_transaction(
            repo, (), {}, expected_roots, publications
        )


def test_verified_immutable_object_publication_is_not_treated_as_mutable_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    expected_roots, publications = _plan()
    batch = DownloadedPackfileUriBatch((), {}, 0)
    staged = StagedPackfileUriImport({NATIVE: LOCAL}, (LOCAL,))
    certificate = PackfileUriRootCertificate({NATIVE: LOCAL}, {NATIVE: b"commit"})

    monkeypatch.setattr(transaction, "download_packfile_uris", lambda *a, **k: batch)

    def stage_with_immutable_write(*args, **kwargs):
        probe = repo.pygit_dir / "objects" / "phase325" / "immutable-probe"
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(b"content-addressed staging is outside the mutable guard")
        return staged

    monkeypatch.setattr(transaction, "stage_packfile_uri_import", stage_with_immutable_write)
    monkeypatch.setattr(transaction, "certify_packfile_uri_roots", lambda *a, **k: certificate)
    monkeypatch.setattr(
        transaction,
        "publish_packfile_uri_refs",
        lambda *a, **k: {"refs/heads/fetched": LOCAL},
    )

    result = transaction.execute_packfile_uri_fetch_transaction(
        repo, (), {}, expected_roots, publications
    )

    assert result.published_refs == {"refs/heads/fetched": LOCAL}


def test_snapshot_preserves_existing_mutable_bytes_when_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    expected_roots, publications = _plan()
    promisor = repo.pygit_dir / "promisor.json"
    promisor.write_bytes(b'{"promised": {}, "resolved": {}, "sizes": {}}\n')
    _install_successful_prepublication_stages(monkeypatch)
    monkeypatch.setattr(
        transaction,
        "publish_packfile_uri_refs",
        lambda *a, **k: {"refs/heads/fetched": LOCAL},
    )

    transaction.execute_packfile_uri_fetch_transaction(
        repo, (), {}, expected_roots, publications
    )

    assert promisor.read_bytes() == b'{"promised": {}, "resolved": {}, "sizes": {}}\n'
