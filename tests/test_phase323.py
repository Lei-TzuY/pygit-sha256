from pathlib import Path

import pytest

from pygit import Repository
from pygit.objects.blob import BlobObject
from pygit.protocol_v2_packfile_uri_connectivity import PackfileUriRootCertificate
from pygit.protocol_v2_packfile_uri_refs import (
    PackfileUriRefPublication,
    publish_packfile_uri_refs,
)
from pygit.refs import ZERO_SHA


NATIVE_A = "a" * 40
NATIVE_B = "b" * 40


def _commit(repo: Repository, path: str, text: str, message: str) -> str:
    target = repo.worktree / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    repo.add([path])
    return repo.commit(message)


def test_publishes_certified_commit_as_final_cas_step(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    tip = _commit(repo, "a.txt", "one\n", "tip")
    certificate = PackfileUriRootCertificate({NATIVE_A: tip}, {NATIVE_A: b"commit"})

    result = publish_packfile_uri_refs(
        repo,
        certificate,
        {"refs/heads/fetched": PackfileUriRefPublication(NATIVE_A, ZERO_SHA)},
    )

    assert result == {"refs/heads/fetched": tip}
    assert repo.refs.resolve("refs/heads/fetched") == tip
    assert not (repo.pygit_dir / "refs" / "heads" / "fetched.lock").exists()


def test_stale_expected_old_oid_aborts_without_partial_publication(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    old = _commit(repo, "a.txt", "one\n", "old")
    repo.branch("existing")
    new = _commit(repo, "a.txt", "two\n", "new")
    certificate = PackfileUriRootCertificate(
        {NATIVE_A: new, NATIVE_B: new},
        {NATIVE_A: b"commit", NATIVE_B: b"commit"},
    )

    with pytest.raises(RuntimeError, match="expected"):
        publish_packfile_uri_refs(
            repo,
            certificate,
            {
                "refs/heads/new-ref": PackfileUriRefPublication(NATIVE_A, ZERO_SHA),
                "refs/heads/existing": PackfileUriRefPublication(NATIVE_B, new),
            },
        )

    assert repo.refs.resolve("refs/heads/new-ref") is None
    assert repo.refs.resolve("refs/heads/existing") == old


def test_existing_canonical_lock_blocks_publication(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    tip = _commit(repo, "a.txt", "one\n", "tip")
    certificate = PackfileUriRootCertificate({NATIVE_A: tip}, {NATIVE_A: b"commit"})
    lock = repo.pygit_dir / "refs" / "heads" / "fetched.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("native git owns this lock\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="lock file already exists"):
        publish_packfile_uri_refs(
            repo,
            certificate,
            {"refs/heads/fetched": PackfileUriRefPublication(NATIVE_A, ZERO_SHA)},
        )

    assert repo.refs.resolve("refs/heads/fetched") is None
    assert lock.read_text(encoding="utf-8") == "native git owns this lock\n"


def test_branch_publication_requires_certified_commit(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    blob = repo.store.write(BlobObject(b"not a branch tip"))
    certificate = PackfileUriRootCertificate({NATIVE_A: blob}, {NATIVE_A: b"blob"})

    with pytest.raises(ValueError, match="certified commit"):
        publish_packfile_uri_refs(
            repo,
            certificate,
            {"refs/heads/fetched": PackfileUriRefPublication(NATIVE_A, ZERO_SHA)},
        )

    assert repo.refs.resolve("refs/heads/fetched") is None


def test_revalidates_certificate_object_type_before_locking(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    blob = repo.store.write(BlobObject(b"taggable"))
    certificate = PackfileUriRootCertificate({NATIVE_A: blob}, {NATIVE_A: b"commit"})

    with pytest.raises(ValueError, match="changed Git object type"):
        publish_packfile_uri_refs(
            repo,
            certificate,
            {"refs/tags/test": PackfileUriRefPublication(NATIVE_A, ZERO_SHA)},
        )

    assert not (repo.pygit_dir / "refs" / "tags" / "test.lock").exists()
    assert repo.refs.resolve("refs/tags/test") is None


def test_successful_multi_ref_publication_releases_all_locks(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    tip = _commit(repo, "a.txt", "one\n", "tip")
    certificate = PackfileUriRootCertificate(
        {NATIVE_A: tip, NATIVE_B: tip},
        {NATIVE_A: b"commit", NATIVE_B: b"commit"},
    )

    result = publish_packfile_uri_refs(
        repo,
        certificate,
        {
            "refs/heads/one": PackfileUriRefPublication(NATIVE_A, ZERO_SHA),
            "refs/heads/two": PackfileUriRefPublication(NATIVE_B, ZERO_SHA),
        },
    )

    assert result == {"refs/heads/one": tip, "refs/heads/two": tip}
    assert repo.refs.resolve("refs/heads/one") == tip
    assert repo.refs.resolve("refs/heads/two") == tip
    assert not (repo.pygit_dir / "refs" / "heads" / "one.lock").exists()
    assert not (repo.pygit_dir / "refs" / "heads" / "two.lock").exists()


@pytest.mark.parametrize("native", ["a" * 39, "g" * 40, "a" * 64])
def test_rejects_non_native_publication_oid(tmp_path: Path, native: str) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    tip = _commit(repo, "a.txt", "one\n", "tip")
    certificate = PackfileUriRootCertificate({native: tip}, {native: b"commit"})

    with pytest.raises(ValueError, match="native id"):
        publish_packfile_uri_refs(
            repo,
            certificate,
            {"refs/heads/fetched": PackfileUriRefPublication(native, ZERO_SHA)},
        )


@pytest.mark.parametrize("old", ["1" * 63, "g" * 64, "1" * 40])
def test_rejects_non_sha256_expected_old_value(tmp_path: Path, old: str) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    tip = _commit(repo, "a.txt", "one\n", "tip")
    certificate = PackfileUriRootCertificate({NATIVE_A: tip}, {NATIVE_A: b"commit"})

    with pytest.raises(ValueError, match="expected old local id"):
        publish_packfile_uri_refs(
            repo,
            certificate,
            {"refs/heads/fetched": PackfileUriRefPublication(NATIVE_A, old)},
        )


def test_rejects_non_refs_name_and_empty_transaction(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    tip = _commit(repo, "a.txt", "one\n", "tip")
    certificate = PackfileUriRootCertificate({NATIVE_A: tip}, {NATIVE_A: b"commit"})

    with pytest.raises(ValueError, match="full refs"):
        publish_packfile_uri_refs(
            repo,
            certificate,
            {"HEAD": PackfileUriRefPublication(NATIVE_A, ZERO_SHA)},
        )
    with pytest.raises(ValueError, match="at least one ref"):
        publish_packfile_uri_refs(repo, certificate, {})
