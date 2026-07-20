"""Unit tests for the object model (blob, tree, commit)."""

import time
import pytest

from pygit.objects import (
    BlobObject,
    CommitObject,
    Identity,
    TreeEntry,
    TreeObject,
)


# ------------------------------------------------------------------
# BlobObject
# ------------------------------------------------------------------

class TestBlob:
    def test_roundtrip(self):
        blob = BlobObject(b"hello, world\n")
        out  = BlobObject()
        out.deserialize(blob.serialize())
        assert out.data == b"hello, world\n"

    def test_empty_blob(self):
        blob = BlobObject(b"")
        assert len(blob) == 0
        assert blob.hash() == BlobObject(b"").hash()

    def test_hash_is_deterministic(self):
        assert BlobObject(b"abc").hash() == BlobObject(b"abc").hash()

    def test_different_content_different_hash(self):
        assert BlobObject(b"abc").hash() != BlobObject(b"ABC").hash()

    def test_type_name(self):
        assert BlobObject.type_name == b"blob"

    def test_len(self):
        data = b"x" * 100
        assert len(BlobObject(data)) == 100


# ------------------------------------------------------------------
# TreeObject
# ------------------------------------------------------------------

class TestTree:
    def _make_tree(self):
        t = TreeObject()
        sha_a = BlobObject(b"file a").hash()
        sha_b = BlobObject(b"file b").hash()
        t.add_entry("100644", "alpha.txt", sha_a)
        t.add_entry("100644", "beta.txt",  sha_b)
        return t, sha_a, sha_b

    def test_roundtrip(self):
        t, sha_a, sha_b = self._make_tree()
        raw = t.serialize()
        t2  = TreeObject()
        t2.deserialize(raw)
        names = [e.name for e in t2.entries]
        assert "alpha.txt" in names
        assert "beta.txt"  in names

    def test_entry_sha_preserved(self):
        t, sha_a, _ = self._make_tree()
        raw = t.serialize()
        t2  = TreeObject()
        t2.deserialize(raw)
        found = {e.name: e.sha for e in t2.entries}
        assert found["alpha.txt"] == sha_a

    def test_add_entry_deduplicates(self):
        t = TreeObject()
        sha = BlobObject(b"v1").hash()
        t.add_entry("100644", "file.txt", sha)
        new_sha = BlobObject(b"v2").hash()
        t.add_entry("100644", "file.txt", new_sha)
        assert len(t) == 1
        assert t.entries[0].sha == new_sha

    def test_is_dir(self):
        entry = TreeEntry(mode="040000", name="src", sha="a" * 64)
        assert entry.is_dir
        assert not entry.is_executable
        assert not entry.is_symlink

    def test_is_executable(self):
        entry = TreeEntry(mode="100755", name="run.sh", sha="b" * 64)
        assert entry.is_executable
        assert not entry.is_dir

    def test_sorted_on_serialize(self):
        t = TreeObject()
        sha = BlobObject(b"x").hash()
        t.add_entry("100644", "zzz.txt", sha)
        t.add_entry("100644", "aaa.txt", sha)
        t.add_entry("100644", "mmm.txt", sha)
        t.deserialize(t.serialize())
        assert [e.name for e in t.entries] == ["aaa.txt", "mmm.txt", "zzz.txt"]


# ------------------------------------------------------------------
# CommitObject
# ------------------------------------------------------------------

class TestCommit:
    def _make_commit(self, parents=None):
        tree_sha = TreeObject().hash()
        author   = Identity("Alice", "alice@example.com", timestamp=1_700_000_000)
        return CommitObject(
            tree=tree_sha,
            parents=parents or [],
            author=author,
            committer=author,
            message="Initial commit",
        )

    def test_roundtrip(self):
        c   = self._make_commit()
        raw = c.serialize()
        c2  = CommitObject()
        c2.deserialize(raw)
        assert c2.tree    == c.tree
        assert c2.message == "Initial commit"
        assert c2.author.name  == "Alice"
        assert c2.author.email == "alice@example.com"

    def test_parent_roundtrip(self):
        parent_sha = "a" * 64
        c  = self._make_commit(parents=[parent_sha])
        c2 = CommitObject()
        c2.deserialize(c.serialize())
        assert c2.parents == [parent_sha]

    def test_no_parents_for_root(self):
        c = self._make_commit()
        assert c.parents == []

    def test_identity_encode_decode(self):
        ident = Identity("Bob", "bob@example.com", timestamp=1_234_567, timezone="+0800")
        ident2 = Identity.decode(ident.encode())
        assert ident2.name      == "Bob"
        assert ident2.email     == "bob@example.com"
        assert ident2.timestamp == 1_234_567
        assert ident2.timezone  == "+0800"

    def test_hash_changes_with_message(self):
        c1 = self._make_commit()
        c1.message = "msg A"
        c2 = self._make_commit()
        c2.message = "msg B"
        assert c1.hash() != c2.hash()

    def test_type_name(self):
        assert CommitObject.type_name == b"commit"
