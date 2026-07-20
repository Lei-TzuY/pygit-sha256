"""Integration tests for the Repository class."""

import os
import tempfile
from pathlib import Path

import pytest

from pygit import Repository


@pytest.fixture
def repo(tmp_path):
    """An initialised repository with a couple of test files."""
    r = Repository.init(str(tmp_path))
    (tmp_path / "hello.txt").write_text("Hello, world!\n", encoding="utf-8")
    (tmp_path / "data.bin").write_bytes(bytes(range(256)))
    return r


# ------------------------------------------------------------------
# init
# ------------------------------------------------------------------

class TestInit:
    def test_creates_pygit_dir(self, tmp_path):
        r = Repository.init(str(tmp_path))
        assert (tmp_path / ".pygit").is_dir()

    def test_creates_objects_dir(self, tmp_path):
        Repository.init(str(tmp_path))
        assert (tmp_path / ".pygit" / "objects").is_dir()

    def test_head_points_to_main(self, tmp_path):
        Repository.init(str(tmp_path))
        head = (tmp_path / ".pygit" / "HEAD").read_text()
        assert "refs/heads/main" in head

    def test_reinit_does_not_raise(self, tmp_path):
        Repository.init(str(tmp_path))
        Repository.init(str(tmp_path))  # should not raise

    def test_reinit_preserves_head(self, tmp_path):
        Repository.init(str(tmp_path))
        head = tmp_path / ".pygit" / "HEAD"
        head.write_text("ref: refs/heads/feature")
        Repository.init(str(tmp_path))
        assert head.read_text() == "ref: refs/heads/feature"

    def test_open_nonexistent_raises(self, tmp_path):
        with pytest.raises(RuntimeError, match="Not a pygit repository"):
            Repository(str(tmp_path / "nonexistent"))


# ------------------------------------------------------------------
# hash_object / cat_file
# ------------------------------------------------------------------

class TestHashAndCat:
    def test_hash_object_no_write(self, repo):
        sha = repo.hash_object(b"test data", write=False)
        assert len(sha) == 64
        assert not repo.store.exists(sha)

    def test_hash_object_with_write(self, repo):
        sha = repo.hash_object(b"test data", write=True)
        assert repo.store.exists(sha)

    def test_cat_file_returns_blob(self, repo):
        sha = repo.hash_object(b"cat me", write=True)
        from pygit.objects import BlobObject
        obj = repo.cat_file(sha)
        assert isinstance(obj, BlobObject)
        assert obj.data == b"cat me"


# ------------------------------------------------------------------
# add / rm
# ------------------------------------------------------------------

class TestAddRm:
    def test_add_single_file(self, repo):
        repo.add(["hello.txt"])
        assert "hello.txt" in repo.index

    def test_add_multiple_files(self, repo):
        repo.add(["hello.txt", "data.bin"])
        assert "hello.txt" in repo.index
        assert "data.bin"  in repo.index

    def test_add_stores_blob(self, repo):
        repo.add(["hello.txt"])
        sha = repo.index.get("hello.txt").sha  # type: ignore[union-attr]
        assert repo.store.exists(sha)

    def test_add_directory(self, tmp_path):
        r = Repository.init(str(tmp_path))
        sub = tmp_path / "src"
        sub.mkdir()
        (sub / "a.py").write_text("a")
        (sub / "b.py").write_text("b")
        r.add(["src"])
        assert "src/a.py" in r.index
        assert "src/b.py" in r.index

    def test_add_missing_path_raises(self, repo):
        with pytest.raises(FileNotFoundError):
            repo.add(["nonexistent.txt"])

    def test_rm_cached(self, repo):
        repo.add(["hello.txt"])
        repo.rm("hello.txt", cached=True)
        assert "hello.txt" not in repo.index
        assert (repo.worktree / "hello.txt").exists()  # file still on disk

    def test_rm_deletes_file(self, repo):
        repo.add(["hello.txt"])
        repo.rm("hello.txt")
        assert "hello.txt" not in repo.index
        assert not (repo.worktree / "hello.txt").exists()

    def test_rm_missing_raises(self, repo):
        with pytest.raises(KeyError):
            repo.rm("not_staged.txt")


# ------------------------------------------------------------------
# commit
# ------------------------------------------------------------------

class TestCommit:
    def test_basic_commit(self, repo):
        repo.add(["hello.txt"])
        sha = repo.commit("First commit", author_name="Alice", author_email="a@a.com")
        assert len(sha) == 64
        assert repo.refs.resolve_head() == sha

    def test_commit_advances_branch(self, repo):
        repo.add(["hello.txt"])
        sha1 = repo.commit("c1")
        repo.add(["data.bin"])
        sha2 = repo.commit("c2")
        assert repo.refs.get_branch("main") == sha2
        assert sha1 != sha2

    def test_commit_empty_index_raises(self, repo):
        with pytest.raises(RuntimeError, match="Nothing to commit"):
            repo.commit("empty")

    def test_commit_stores_tree(self, repo):
        repo.add(["hello.txt"])
        sha = repo.commit("t")
        from pygit.objects import CommitObject, TreeObject
        obj = repo.store.read(sha)
        assert isinstance(obj, CommitObject)
        tree = repo.store.read(obj.tree)
        assert isinstance(tree, TreeObject)
        names = [e.name for e in tree.entries]
        assert "hello.txt" in names

    def test_commit_nested_paths(self, tmp_path):
        r = Repository.init(str(tmp_path))
        sub = tmp_path / "src" / "util"
        sub.mkdir(parents=True)
        (sub / "helper.py").write_text("pass")
        r.add(["src"])
        sha = r.commit("nested")
        from pygit.objects import CommitObject
        c = r.store.read(sha)
        assert isinstance(c, CommitObject)
        # The root tree should contain "src" subtree
        from pygit.objects import TreeObject
        root_tree = r.store.read(c.tree)
        assert isinstance(root_tree, TreeObject)
        names = [e.name for e in root_tree.entries]
        assert "src" in names

    def test_parent_link(self, repo):
        repo.add(["hello.txt"])
        sha1 = repo.commit("c1")
        repo.add(["data.bin"])
        sha2 = repo.commit("c2")
        from pygit.objects import CommitObject
        c2 = repo.store.read(sha2)
        assert isinstance(c2, CommitObject)
        assert sha1 in c2.parents


# ------------------------------------------------------------------
# log
# ------------------------------------------------------------------

class TestLog:
    def test_empty_log(self, repo):
        assert repo.log() == []

    def test_single_commit(self, repo):
        repo.add(["hello.txt"])
        sha = repo.commit("first")
        log = repo.log()
        assert len(log) == 1
        assert log[0][0] == sha

    def test_log_order(self, repo):
        repo.add(["hello.txt"])
        sha1 = repo.commit("c1")
        repo.add(["data.bin"])
        sha2 = repo.commit("c2")
        log = repo.log()
        assert log[0][0] == sha2
        assert log[1][0] == sha1

    def test_max_count(self, repo):
        repo.add(["hello.txt"])
        repo.commit("c1")
        repo.add(["data.bin"])
        repo.commit("c2")
        assert len(repo.log(max_count=1)) == 1


# ------------------------------------------------------------------
# status
# ------------------------------------------------------------------

class TestStatus:
    def test_clean_status(self, repo):
        repo.add(["hello.txt"])
        repo.commit("c1")
        st = repo.status()
        assert st["staged"]   == []
        assert st["unstaged"] == []

    def test_new_staged_file(self, repo):
        repo.add(["hello.txt"])
        st = repo.status()
        assert any(k == "new file" and p == "hello.txt" for k, p in st["staged"])

    def test_modified_staged(self, repo):
        repo.add(["hello.txt"])
        repo.commit("c1")
        (repo.worktree / "hello.txt").write_text("changed\n", encoding="utf-8")
        repo.add(["hello.txt"])
        st = repo.status()
        assert any(k == "modified" and p == "hello.txt" for k, p in st["staged"])

    def test_unstaged_modified(self, repo):
        repo.add(["hello.txt"])
        repo.commit("c1")
        (repo.worktree / "hello.txt").write_text("changed\n", encoding="utf-8")
        st = repo.status()
        assert any(k == "modified" and p == "hello.txt" for k, p in st["unstaged"])

    def test_untracked_files(self, repo):
        st = repo.status()
        assert "hello.txt" in st["untracked"]
        assert "data.bin"  in st["untracked"]

    def test_branch_name(self, repo):
        st = repo.status()
        assert st["branch"] == "main"


# ------------------------------------------------------------------
# diff
# ------------------------------------------------------------------

class TestDiff:
    def test_diff_shows_modification(self, repo):
        repo.add(["hello.txt"])
        repo.commit("c1")
        (repo.worktree / "hello.txt").write_text("New content\n", encoding="utf-8")
        diff = repo.diff()
        assert "hello.txt" in diff
        assert "New content" in diff

    def test_diff_empty_when_clean(self, repo):
        repo.add(["hello.txt"])
        repo.commit("c1")
        assert repo.diff() == ""

    def test_diff_cached(self, repo):
        repo.add(["hello.txt"])
        diff = repo.diff(cached=True)
        assert "hello.txt" in diff

    def test_diff_cached_empty_after_commit(self, repo):
        repo.add(["hello.txt"])
        repo.commit("c1")
        assert repo.diff(cached=True) == ""


# ------------------------------------------------------------------
# branch / checkout / tag
# ------------------------------------------------------------------

class TestBranchCheckoutTag:
    def test_list_branches_initially_empty(self, repo):
        assert repo.branch() == []

    def test_create_branch(self, repo):
        repo.add(["hello.txt"])
        repo.commit("c1")
        repo.branch("dev")
        assert "dev" in repo.refs.list_branches()

    def test_delete_branch(self, repo):
        repo.add(["hello.txt"])
        repo.commit("c1")
        repo.branch("dev")
        repo.branch("dev", delete=True)
        assert "dev" not in repo.refs.list_branches()

    def test_checkout_branch(self, tmp_path):
        r = Repository.init(str(tmp_path))
        (tmp_path / "v1.txt").write_text("version 1\n")
        r.add(["v1.txt"])
        r.commit("c1")

        r.branch("feature")
        (tmp_path / "v2.txt").write_text("version 2\n")
        r.add(["v2.txt"])
        r.commit("c2")

        r.checkout("main")  # go back — v2.txt should disappear
        assert not (tmp_path / "v2.txt").exists()
        assert (tmp_path / "v1.txt").exists()
        assert r.refs.current_branch() == "main"

    def test_checkout_restores_files(self, tmp_path):
        r = Repository.init(str(tmp_path))
        (tmp_path / "a.txt").write_text("original\n")
        r.add(["a.txt"])
        r.commit("base")

        r.branch("edit")
        (tmp_path / "a.txt").write_text("modified\n")
        r.add(["a.txt"])
        r.commit("edit-commit")

        r.checkout("main")
        assert (tmp_path / "a.txt").read_text() == "original\n"

    def test_tag_create_and_list(self, repo):
        repo.add(["hello.txt"])
        repo.commit("c1")
        repo.tag("v1.0")
        assert "v1.0" in repo.tag()

    def test_tag_resolves(self, repo):
        repo.add(["hello.txt"])
        sha = repo.commit("c1")
        repo.tag("v1.0")
        assert repo.refs.get_tag("v1.0") == sha
