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


def _guard_lock_names(repo: Repository) -> set[str]:
    return {
        path.relative_to(repo.pygit_dir).as_posix()
        for path in transaction._publication_guard_lock_paths(repo)
    }


def test_publication_holds_repository_metadata_locks_until_ref_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    expected_roots, publications = _plan()
    _install_successful_prepublication_stages(monkeypatch)
    seen: set[str] = set()

    def publish(*args, **kwargs):
        for path in transaction._publication_guard_lock_paths(repo):
            assert path.read_bytes() == b"packfile-uri publication guard\n"
            seen.add(path.relative_to(repo.pygit_dir).as_posix())
        return {"refs/heads/fetched": LOCAL}

    monkeypatch.setattr(transaction, "publish_packfile_uri_refs", publish)

    result = transaction.execute_packfile_uri_fetch_transaction(
        repo, (), {}, expected_roots, publications
    )

    assert result.published_refs == {"refs/heads/fetched": LOCAL}
    assert seen == {"HEAD.lock", "packed-refs.lock", "promisor.json.lock", "shallow.lock"}
    assert all(not path.exists() for path in transaction._publication_guard_lock_paths(repo))


@pytest.mark.parametrize(
    "lock_name",
    ["HEAD.lock", "packed-refs.lock", "promisor.json.lock", "shallow.lock"],
)
def test_existing_metadata_lock_aborts_without_stealing_lock_or_publishing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lock_name: str,
) -> None:
    repo = _repo(tmp_path)
    expected_roots, publications = _plan()
    _install_successful_prepublication_stages(monkeypatch)
    existing = repo.pygit_dir / lock_name
    existing.write_bytes(b"concurrent writer\n")

    monkeypatch.setattr(
        transaction,
        "publish_packfile_uri_refs",
        lambda *a, **k: pytest.fail("lock contention must abort before ref publication"),
    )

    with pytest.raises(RuntimeError, match="lock file already exists"):
        transaction.execute_packfile_uri_fetch_transaction(
            repo, (), {}, expected_roots, publications
        )

    assert existing.read_bytes() == b"concurrent writer\n"
    for path in transaction._publication_guard_lock_paths(repo):
        if path != existing:
            assert not path.exists()


def test_guard_locks_are_released_when_ref_publication_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    expected_roots, publications = _plan()
    _install_successful_prepublication_stages(monkeypatch)

    def fail_publish(*args, **kwargs):
        assert all(path.exists() for path in transaction._publication_guard_lock_paths(repo))
        raise RuntimeError("phase326 injected CAS failure")

    monkeypatch.setattr(transaction, "publish_packfile_uri_refs", fail_publish)

    with pytest.raises(RuntimeError, match="injected CAS failure"):
        transaction.execute_packfile_uri_fetch_transaction(
            repo, (), {}, expected_roots, publications
        )

    assert all(not path.exists() for path in transaction._publication_guard_lock_paths(repo))


def test_state_change_before_guard_acquisition_is_still_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    expected_roots, publications = _plan()
    _install_successful_prepublication_stages(monkeypatch)
    original_acquire = transaction._acquire_publication_guard_locks

    def mutate_then_lock(repo_arg):
        repo.pygit_dir.joinpath("HEAD").write_text(
            "ref: refs/heads/concurrent\n", encoding="utf-8"
        )
        return original_acquire(repo_arg)

    monkeypatch.setattr(transaction, "_acquire_publication_guard_locks", mutate_then_lock)
    monkeypatch.setattr(
        transaction,
        "publish_packfile_uri_refs",
        lambda *a, **k: pytest.fail("snapshot mismatch must abort before publication"),
    )

    with pytest.raises(RuntimeError, match="HEAD"):
        transaction.execute_packfile_uri_fetch_transaction(
            repo, (), {}, expected_roots, publications
        )

    assert all(not path.exists() for path in transaction._publication_guard_lock_paths(repo))


def test_guard_scope_does_not_duplicate_phase323_target_ref_lock(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)

    assert _guard_lock_names(repo) == {
        "HEAD.lock",
        "packed-refs.lock",
        "promisor.json.lock",
        "shallow.lock",
    }
    assert "refs/heads/fetched.lock" not in _guard_lock_names(repo)
