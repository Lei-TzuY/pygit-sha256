"""Integration tests for Phase 4 pygit features: annotated tags, fsck, gc, archive."""

import zipfile
from pathlib import Path
import pytest
from pygit import Repository
from pygit.objects import TagObject, BlobObject, Identity


def _commit_file(repo: Repository, path: str, content: str, message: str) -> str:
    target = repo.worktree / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    repo.add([path])
    return repo.commit(message)


class TestAnnotatedTagObject:
    def test_tag_object_roundtrip(self):
        tagger = Identity("Alice", "alice@example.com", 1700000000, "+0000")
        tag = TagObject(
            target_sha="a" * 64,
            target_type=b"commit",
            tag_name="v1.0",
            tagger=tagger,
            message="Release 1.0 notes",
        )
        data = tag.serialize()
        restored = TagObject()
        restored.deserialize(data)

        assert restored.target_sha == "a" * 64
        assert restored.target_type == b"commit"
        assert restored.tag_name == "v1.0"
        assert restored.tagger.name == "Alice"
        assert restored.message == "Release 1.0 notes"

    def test_create_annotated_tag(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        commit_sha = _commit_file(repo, "a.txt", "hello", "c1")

        repo.tag("v1.0", annotated=True, message="Release 1.0")
        tag_sha = repo.refs.get_tag("v1.0")
        assert tag_sha is not None
        assert tag_sha != commit_sha  # Tag object SHA is different from target commit SHA

        tag_obj = repo.store.read(tag_sha)
        assert isinstance(tag_obj, TagObject)
        assert tag_obj.target_sha == commit_sha
        assert tag_obj.message == "Release 1.0"


class TestFsckAndGC:
    def test_fsck_and_gc_dangling_objects(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        _commit_file(repo, "a.txt", "hello", "c1")

        # Manually create a dangling blob object in store
        dangling_blob = BlobObject(b"dangling content")
        dangling_sha = repo.store.write(dangling_blob)
        assert repo.store.exists(dangling_sha)

        # Check fsck
        res = repo.fsck()
        assert dangling_sha in res["dangling"]

        # Run garbage collection
        gc_res = repo.gc(prune=True)
        assert gc_res["deleted"] >= 1
        assert not repo.store.exists(dangling_sha)

        # Check fsck clean
        res_after = repo.fsck()
        assert dangling_sha not in res_after["dangling"]


class TestArchive:
    def test_archive_zip_export(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        repo.worktree / "src/main.py"
        (tmp_path / "src" / "main.py").parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "src" / "main.py").write_bytes(b"print('hello')\n")
        repo.add(["src/main.py"])
        repo.commit("c1")

        (tmp_path / "README.md").write_bytes(b"# Project\n")
        repo.add(["README.md"])
        repo.commit("c2")

        zip_out = tmp_path / "export.zip"
        repo.archive(str(zip_out), format="zip")
        assert zip_out.exists()

        with zipfile.ZipFile(zip_out, "r") as zf:
            names = zf.namelist()
            assert "src/main.py" in names
            assert "README.md" in names
            assert zf.read("src/main.py") == b"print('hello')\n"
