import hashlib

import pytest

from pygit.protocol_v2_packfile_uri_batch import DownloadedPackfileUriBatch
from pygit.protocol_v2_packfile_uri_stage import stage_packfile_uri_import
from pygit.remote import NativeObject
from pygit.store import ObjectStore


def _native(type_name: str, data: bytes) -> NativeObject:
    canonical = f"{type_name} {len(data)}\0".encode() + data
    oid = hashlib.sha1(canonical).hexdigest()
    return NativeObject(type_name, data, oid)


def _batch(objects):
    return DownloadedPackfileUriBatch((), dict(objects), 0)


def _files(root):
    return sorted(path for path in root.rglob("*") if path.is_file())


def test_stages_complete_inline_external_graph_before_publication(tmp_path):
    blob = _native("blob", b"external payload\n")
    tree = _native(
        "tree",
        b"100644 payload.txt\x00" + bytes.fromhex(blob.oid),
    )
    store = ObjectStore(tmp_path / "objects")

    result = stage_packfile_uri_import(
        store,
        {tree.oid: tree},
        _batch({blob.oid: blob}),
    )

    assert set(result.native_to_local) == {tree.oid, blob.oid}
    assert len(result.local_oids) == 2
    for local_oid in result.local_oids:
        assert len(local_oid) == 64
        int(local_oid, 16)
        assert store.read(local_oid) is not None


def test_missing_cross_pack_dependency_fails_before_destination_write(tmp_path):
    missing = "12" * 20
    tree = _native("tree", b"100644 missing.txt\x00" + bytes.fromhex(missing))
    store = ObjectStore(tmp_path / "objects")

    with pytest.raises(KeyError, match="missing object"):
        stage_packfile_uri_import(store, {tree.oid: tree}, _batch({}))

    assert _files(store.root) == []


def test_invalid_native_identity_fails_before_destination_write(tmp_path):
    blob = _native("blob", b"payload")
    forged = NativeObject("blob", b"changed", blob.oid)
    store = ObjectStore(tmp_path / "objects")

    with pytest.raises(ValueError, match="content does not match"):
        stage_packfile_uri_import(store, {blob.oid: forged}, _batch({}))

    assert _files(store.root) == []


def test_inline_external_duplicate_identical_object_is_deduplicated(tmp_path):
    blob = _native("blob", b"same")
    store = ObjectStore(tmp_path / "objects")

    result = stage_packfile_uri_import(
        store,
        {blob.oid: blob},
        _batch({blob.oid: blob}),
    )

    assert result.native_to_local.keys() == {blob.oid}
    assert len(result.local_oids) == 1
    assert len(_files(store.root)) == 1


def test_empty_combined_object_set_is_rejected_without_writes(tmp_path):
    store = ObjectStore(tmp_path / "objects")

    with pytest.raises(ValueError, match="at least one native object"):
        stage_packfile_uri_import(store, {}, _batch({}))

    assert _files(store.root) == []


def test_local_sha256_is_content_derived_not_native_oid_padding(tmp_path):
    blob = _native("blob", b"sha256 boundary")
    store = ObjectStore(tmp_path / "objects")

    result = stage_packfile_uri_import(store, {blob.oid: blob}, _batch({}))
    local_oid = result.native_to_local[blob.oid]

    assert len(blob.oid) == 40
    assert len(local_oid) == 64
    assert local_oid != blob.oid.ljust(64, "0")
    assert local_oid != blob.oid.rjust(64, "0")
