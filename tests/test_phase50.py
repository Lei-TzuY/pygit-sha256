"""Phase 50 tests: index-to-tree and tree-to-commit plumbing."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from pygit import Repository
from pygit.command import dispatch
from pygit.commit_plumbing import commit_tree, write_tree
from pygit.index import IndexEntry
from pygit.objects import BlobObject, CommitObject, TreeObject


def _repo(tmp_path: Path) -> Repository:
    repo = Repository.init(str(tmp_path / "r"))
    (repo.worktree / "README.md").write_text("root\n", encoding="utf-8")
    (repo.worktree / "src").mkdir()
    (repo.worktree / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (repo.worktree / "src" / "lib").mkdir()
    (repo.worktree / "src" / "lib" / "util.py").write_text("VALUE = 1\n", encoding="utf-8")
    repo.add(["README.md", "src/main.py", "src/lib/util.py"])
    return repo


def _fixed_env() -> dict[str, str]:
    return {
        "GIT_AUTHOR_NAME": "A U Thor",
        "GIT_AUTHOR_EMAIL": "author@example.com",
        "GIT_AUTHOR_DATE": "1700000000 +0800",
        "GIT_COMMITTER_NAME": "C O Mitter",
        "GIT_COMMITTER_EMAIL": "committer@example.com",
        "GIT_COMMITTER_DATE": "1700000010 +0800",
    }


class TestWriteTree:
    def test_writes_nested_tree_from_index_without_mutating_index(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        before = [(e.path, e.sha, e.mode) for e in repo.index.all_entries()]

        root_oid = write_tree(repo)
        root = repo.store.read(root_oid)

        assert isinstance(root, TreeObject)
        assert [e.name for e in root.entries] == ["README.md", "src"]
        src_entry = next(e for e in root.entries if e.name == "src")
        assert src_entry.mode == "040000"
        src = repo.store.read(src_entry.sha)
        assert isinstance(src, TreeObject)
        assert [e.name for e in src.entries] == ["lib", "main.py"]
        assert [(e.path, e.sha, e.mode) for e in repo.index.all_entries()] == before

    def test_empty_index_produces_empty_tree(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "empty"))
        oid = write_tree(repo)
        obj = repo.store.read(oid)
        assert isinstance(obj, TreeObject)
        assert obj.entries == []

    def test_prefix_writes_only_selected_subtree(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        oid = write_tree(repo, prefix="src")
        tree = repo.store.read(oid)
        assert isinstance(tree, TreeObject)
        assert [e.name for e in tree.entries] == ["lib", "main.py"]
        with pytest.raises(KeyError, match="no index entries"):
            write_tree(repo, prefix="missing")

    def test_missing_ok_controls_absent_objects(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        missing_oid = "a" * 64
        repo.index.entries["ghost.txt"] = IndexEntry("ghost.txt", missing_oid, "100644")
        repo.index.save()

        with pytest.raises(KeyError, match="Object not found"):
            write_tree(repo)

        oid = write_tree(repo, missing_ok=True)
        root = repo.store.read(oid)
        assert isinstance(root, TreeObject)
        assert any(e.name == "ghost.txt" and e.sha == missing_oid for e in root.entries)

    def test_rejects_wrong_object_type_and_path_conflicts(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        tree_oid = repo.store.write(TreeObject([]))
        repo.index.entries["bad.txt"] = IndexEntry("bad.txt", tree_oid, "100644")
        with pytest.raises(ValueError, match="must reference a blob"):
            write_tree(repo)

        repo = _repo(tmp_path / "again")
        blob = repo.store.write(BlobObject(b"x"))
        repo.index.entries["src"] = IndexEntry("src", blob, "100644")
        with pytest.raises(ValueError, match="path conflict"):
            write_tree(repo)


class TestCommitTree:
    def test_creates_commit_with_fixed_identity_without_moving_head(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        tree_oid = write_tree(repo)
        head_before = repo.refs.resolve_head()

        oid = commit_tree(repo, tree_oid, message="plumbing commit", env=_fixed_env())
        commit = repo.store.read(oid)

        assert isinstance(commit, CommitObject)
        assert commit.tree == tree_oid
        assert commit.parents == []
        assert commit.message == "plumbing commit"
        assert commit.author.name == "A U Thor"
        assert commit.author.timestamp == 1700000000
        assert commit.author.timezone == "+0800"
        assert commit.committer.name == "C O Mitter"
        assert commit.committer.timestamp == 1700000010
        assert repo.refs.resolve_head() == head_before

    def test_parent_revisions_are_resolved_and_ordered(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        tree_oid = write_tree(repo)
        parent = commit_tree(repo, tree_oid, message="parent", env=_fixed_env())
        repo.refs.set_branch("main", parent)

        env = _fixed_env()
        env["GIT_AUTHOR_DATE"] = "1700000100 +0000"
        env["GIT_COMMITTER_DATE"] = "1700000100 +0000"
        child = commit_tree(repo, tree_oid, parents=["HEAD"], message="child", env=env)
        obj = repo.store.read(child)
        assert isinstance(obj, CommitObject)
        assert obj.parents == [parent]

        with pytest.raises(ValueError, match="duplicate parent"):
            commit_tree(repo, tree_oid, parents=[parent, "HEAD"], env=env)

    def test_rejects_non_tree_and_non_commit_parent(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        blob_oid = repo.store.write(BlobObject(b"not a tree"))
        with pytest.raises(ValueError, match="requires a tree object"):
            commit_tree(repo, blob_oid, env=_fixed_env())

        tree_oid = write_tree(repo)
        with pytest.raises(ValueError):
            commit_tree(repo, tree_oid, parents=[blob_oid], env=_fixed_env())

    def test_same_inputs_are_content_addressed_and_deterministic(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        tree_oid = write_tree(repo)
        first = commit_tree(repo, tree_oid, message="same", env=_fixed_env())
        second = commit_tree(repo, tree_oid, message="same", env=_fixed_env())
        assert first == second


class TestPhase50CLI:
    def test_write_tree_then_commit_tree_from_stdin(self, tmp_path: Path, monkeypatch, capsys) -> None:
        repo = _repo(tmp_path)
        monkeypatch.chdir(repo.worktree)
        capsys.readouterr()

        assert dispatch(["write-tree"]) == 0
        tree_oid = capsys.readouterr().out.strip()
        monkeypatch.setenv("GIT_AUTHOR_NAME", "CLI Author")
        monkeypatch.setenv("GIT_AUTHOR_EMAIL", "cli@example.com")
        monkeypatch.setenv("GIT_AUTHOR_DATE", "1700000200 +0000")
        monkeypatch.setenv("GIT_COMMITTER_DATE", "1700000200 +0000")
        monkeypatch.setattr("sys.stdin", io.StringIO("from stdin\n"))

        assert dispatch(["commit-tree", tree_oid]) == 0
        commit_oid = capsys.readouterr().out.strip()
        obj = repo.store.read(commit_oid)
        assert isinstance(obj, CommitObject)
        assert obj.message == "from stdin"
        assert obj.author.name == "CLI Author"
        assert obj.committer.name == "CLI Author"

    def test_commit_tree_message_paragraphs_and_errors(self, tmp_path: Path, monkeypatch, capsys) -> None:
        repo = _repo(tmp_path)
        monkeypatch.chdir(repo.worktree)
        capsys.readouterr()
        tree_oid = write_tree(repo)
        monkeypatch.setenv("GIT_AUTHOR_DATE", "1700000300 +0000")
        monkeypatch.setenv("GIT_COMMITTER_DATE", "1700000300 +0000")

        assert dispatch(["commit-tree", tree_oid, "-m", "one", "-m", "two"]) == 0
        oid = capsys.readouterr().out.strip()
        obj = repo.store.read(oid)
        assert isinstance(obj, CommitObject)
        assert obj.message == "one\n\ntwo"

        assert dispatch(["commit-tree", repo.index.get("README.md").sha, "-m", "bad"]) == 1
        assert "requires a tree object" in capsys.readouterr().err
