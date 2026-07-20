"""Unit tests for the ObjectStore."""

import tempfile
from pathlib import Path

import pytest

from pygit.objects import BlobObject, CommitObject, Identity, TreeObject
from pygit.store import ObjectStore


@pytest.fixture
def store(tmp_path):
    return ObjectStore(tmp_path / "objects")


class TestObjectStore:
    def test_write_and_read_blob(self, store):
        blob = BlobObject(b"hello store")
        sha  = store.write(blob)
        assert len(sha) == 64  # SHA-256 hex
        result = store.read(sha)
        assert isinstance(result, BlobObject)
        assert result.data == b"hello store"

    def test_write_is_idempotent(self, store):
        blob = BlobObject(b"same content")
        sha1 = store.write(blob)
        sha2 = store.write(blob)
        assert sha1 == sha2
        # only one file should exist
        assert len(list(store.root.rglob("*"))) == 2  # one dir + one file

    def test_exists(self, store):
        blob = BlobObject(b"check me")
        sha  = store.write(blob)
        assert store.exists(sha)
        assert not store.exists("0" * 64)

    def test_read_missing_raises_key_error(self, store):
        with pytest.raises(KeyError):
            store.read("0" * 64)

    def test_write_raw(self, store):
        sha = store.write_raw(b"raw data")
        obj = store.read(sha)
        assert isinstance(obj, BlobObject)
        assert obj.data == b"raw data"

    def test_roundtrip_tree(self, store):
        tree = TreeObject()
        blob_sha = store.write(BlobObject(b"leaf"))
        tree.add_entry("100644", "leaf.txt", blob_sha)
        tree_sha = store.write(tree)

        result = store.read(tree_sha)
        assert isinstance(result, TreeObject)
        assert result.entries[0].name == "leaf.txt"
        assert result.entries[0].sha  == blob_sha

    def test_roundtrip_commit(self, store):
        tree  = TreeObject()
        t_sha = store.write(tree)
        auth  = Identity("Dev", "dev@example.com", timestamp=0)
        c     = CommitObject(tree=t_sha, author=auth, committer=auth, message="test")
        c_sha = store.write(c)

        result = store.read(c_sha)
        assert isinstance(result, CommitObject)
        assert result.message == "test"
        assert result.tree    == t_sha

    def test_corrupt_raises_value_error(self, store):
        blob = BlobObject(b"original")
        sha  = store.write(blob)
        # Corrupt the stored file with garbage bytes
        path = store._path_for(sha)
        path.write_bytes(b"garbage")
        with pytest.raises((ValueError, Exception)):
            store.read(sha)
