from pathlib import Path

import pytest

from pygit.objects.blob import BlobObject
from pygit.protocol_v2_packfile_uri_connectivity import certify_packfile_uri_roots
from pygit.protocol_v2_packfile_uri_stage import StagedPackfileUriImport
from pygit.store import ObjectStore


NATIVE_A = "a" * 40
NATIVE_B = "b" * 40


def _store(tmp_path: Path) -> ObjectStore:
    return ObjectStore(tmp_path / "objects")


def test_certifies_published_root_and_normalizes_type(tmp_path: Path) -> None:
    store = _store(tmp_path)
    local = store.write(BlobObject(b"root"))
    staged = StagedPackfileUriImport({NATIVE_A: local}, (local,))

    result = certify_packfile_uri_roots(store, staged, {NATIVE_A: "blob"})

    assert result.native_to_local == {NATIVE_A: local}
    assert result.expected_types == {NATIVE_A: b"blob"}
    assert len(local) == 64
    assert local != NATIVE_A + ("0" * 24)


def test_rejects_native_root_absent_from_staged_mapping(tmp_path: Path) -> None:
    store = _store(tmp_path)
    local = store.write(BlobObject(b"root"))
    staged = StagedPackfileUriImport({NATIVE_A: local}, (local,))

    with pytest.raises(ValueError, match="not present"):
        certify_packfile_uri_roots(store, staged, {NATIVE_B: b"blob"})


def test_rejects_root_mapping_not_in_published_local_oids(tmp_path: Path) -> None:
    store = _store(tmp_path)
    local = store.write(BlobObject(b"root"))
    staged = StagedPackfileUriImport({NATIVE_A: local}, ())

    with pytest.raises(ValueError, match="not published"):
        certify_packfile_uri_roots(store, staged, {NATIVE_A: b"blob"})


def test_rejects_type_confusion_before_ref_publication(tmp_path: Path) -> None:
    store = _store(tmp_path)
    local = store.write(BlobObject(b"root"))
    staged = StagedPackfileUriImport({NATIVE_A: local}, (local,))

    with pytest.raises(ValueError, match="expected commit, got blob"):
        certify_packfile_uri_roots(store, staged, {NATIVE_A: b"commit"})


def test_rejects_missing_local_object(tmp_path: Path) -> None:
    store = _store(tmp_path)
    local = "1" * 64
    staged = StagedPackfileUriImport({NATIVE_A: local}, (local,))

    with pytest.raises(KeyError):
        certify_packfile_uri_roots(store, staged, {NATIVE_A: b"blob"})


@pytest.mark.parametrize("native", ["a" * 39, "g" * 40, "a" * 64])
def test_rejects_non_native_sha1_root_ids(tmp_path: Path, native: str) -> None:
    store = _store(tmp_path)
    local = store.write(BlobObject(b"root"))
    staged = StagedPackfileUriImport({native: local}, (local,))

    with pytest.raises(ValueError, match="root id"):
        certify_packfile_uri_roots(store, staged, {native: b"blob"})


@pytest.mark.parametrize("expected", [b"", b"commit\n", "future", 123])
def test_rejects_invalid_expected_types(tmp_path: Path, expected) -> None:
    store = _store(tmp_path)
    local = store.write(BlobObject(b"root"))
    staged = StagedPackfileUriImport({NATIVE_A: local}, (local,))

    with pytest.raises(ValueError, match="expected root type"):
        certify_packfile_uri_roots(store, staged, {NATIVE_A: expected})


def test_rejects_empty_root_set(tmp_path: Path) -> None:
    store = _store(tmp_path)
    staged = StagedPackfileUriImport({}, ())

    with pytest.raises(ValueError, match="at least one root"):
        certify_packfile_uri_roots(store, staged, {})
