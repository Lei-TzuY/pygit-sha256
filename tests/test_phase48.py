"""
Phase 48 tests: mktree and read-tree object/index plumbing.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from pygit import Repository
from pygit.entrypoint import dispatch
from pygit.objects import BlobObject, CommitObject, TagObject, TreeObject
from pygit.tree_plumbing import make_tree, read_tree, resolve_treeish


def _commit_file(repo: Repository, path: str, content: str, message: str) -> str:
    target = repo.worktree / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    repo.add([path])
    return repo.commit(
        message,
        author_name="Tester",
        author_email="tester@example.com",
    )


def _tree_of_commit(repo: Repository, commit_oid: str) -> str:
    obj = repo.store.read(commit_oid)
    assert isinstance(obj, CommitObject)
    return obj.tree


class TestMkTree:
    def test_builds_tree_and_validates_types(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        blob_oid = repo.store.write(BlobObject(b"hello\n"))
        nested_blob = repo.store.write(BlobObject(b"nested\n"))
        nested_tree = make_tree(
            repo,
            [f"100644 blob {nested_blob}\tinner.txt"],
        )

        tree_oid = make_tree(
            repo,
            [
                f"040000 tree {nested_tree}\tsrc",
                f"100644 blob {blob_oid}\tREADME.md",
            ],
        )
        tree = repo.store.read(tree_oid)
        assert isinstance(tree, TreeObject)
        assert [(e.mode, e.name, e.sha) for e in tree.entries] == [
            ("100644", "README.md", blob_oid),
            ("040000", "src", nested_tree),
        ]

        with pytest.raises(ValueError, match="requires object type"):
            make_tree(repo, [f"040000 blob {blob_oid}\tbad"])
        with pytest.raises(ValueError, match="expected 'tree'"):
            make_tree(repo, [f"040000 tree {blob_oid}\tbad"])

    def test_missing_duplicate_and_bad_names(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        missing = "a" * 64

        with pytest.raises(KeyError):
            make_tree(repo, [f"100644 blob {missing}\tghost.txt"])

        tree_oid = make_tree(
            repo,
            [f"100644 blob {missing}\tghost.txt"],
            missing=True,
        )
        assert isinstance(repo.store.read(tree_oid), TreeObject)

        blob_oid = repo.store.write(BlobObject(b"x"))
        with pytest.raises(ValueError, match="duplicate"):
            make_tree(
                repo,
                [
                    f"100644 blob {blob_oid}\tsame",
                    f"100644 blob {blob_oid}\tsame",
                ],
            )
        with pytest.raises(ValueError, match="single path component"):
            make_tree(repo, [f"100644 blob {blob_oid}\ta/b"])


class TestReadTree:
    def test_resolves_commit_tree_and_annotated_tag(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        commit_oid = _commit_file(repo, "f.txt", "one\n", "one")
        tree_oid = _tree_of_commit(repo, commit_oid)

        tag = TagObject(
            target_sha=commit_oid,
            target_type=b"commit",
            tag_name="v1",
            message="v1",
        )
        tag_oid = repo.store.write(tag)
        repo.refs.set_tag("v1", tag_oid)

        assert resolve_treeish(repo, tree_oid) == tree_oid
        assert resolve_treeish(repo, commit_oid) == tree_oid
        assert resolve_treeish(repo, "v1") == tree_oid

    def test_replaces_and_clears_index_without_touching_worktree(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        first = _commit_file(repo, "a.txt", "a\n", "first")
        _commit_file(repo, "b.txt", "b\n", "second")

        read_tree(repo, first)
        assert repo.index.paths() == ["a.txt"]
        # Default read-tree is index-only.
        assert (repo.worktree / "b.txt").read_text(encoding="utf-8") == "b\n"

        read_tree(repo, empty=True)
        assert repo.index.paths() == []
        assert (repo.worktree / "a.txt").exists()
        assert (repo.worktree / "b.txt").exists()

    def test_prefix_adds_entries_and_rejects_collisions(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        base = _commit_file(repo, "root.txt", "root\n", "base")
        root_tree = _tree_of_commit(repo, base)

        read_tree(repo, root_tree, prefix="vendor/lib")
        assert repo.index.paths() == ["root.txt", "vendor/lib/root.txt"]

        with pytest.raises(RuntimeError, match="overwrite"):
            read_tree(repo, root_tree, prefix="vendor/lib")

        repo.index.entries.clear()
        repo.index.save()
        blob_oid = repo.store.write(BlobObject(b"file"))
        repo.index.entries["vendor"] = repo._index_entry_for_blob(
            "vendor", blob_oid, "100644"
        )
        repo.index.save()
        with pytest.raises(RuntimeError, match="path conflict"):
            read_tree(repo, root_tree, prefix="vendor")

    def test_update_worktree_materializes_target_and_refuses_dirty_state(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        first = _commit_file(repo, "f.txt", "one\n", "one")
        _commit_file(repo, "f.txt", "two\n", "two")
        assert (repo.worktree / "f.txt").read_text(encoding="utf-8") == "two\n"

        read_tree(repo, first, update_worktree=True)
        assert (repo.worktree / "f.txt").read_text(encoding="utf-8") == "one\n"
        assert repo.index.get("f.txt") is not None

        # Restore HEAD/index/worktree, then make an unstaged change.
        repo.reset("HEAD", mode="hard")
        (repo.worktree / "f.txt").write_text("dirty\n", encoding="utf-8")
        with pytest.raises(RuntimeError, match="local changes"):
            read_tree(repo, first, update_worktree=True)


class TestPhase48CLI:
    def test_mktree_reads_stdin_and_read_tree_empty_dispatches(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        blob_oid = repo.store.write(BlobObject(b"hello"))
        monkeypatch.chdir(repo.worktree)
        capsys.readouterr()

        monkeypatch.setattr(
            "sys.stdin",
            io.StringIO(f"100644 blob {blob_oid}\thello.txt\n"),
        )
        code = dispatch(["mktree"])
        assert code == 0
        tree_oid = capsys.readouterr().out.strip()
        assert isinstance(repo.store.read(tree_oid), TreeObject)

        _commit_file(repo, "tracked.txt", "x\n", "tracked")
        capsys.readouterr()
        code = dispatch(["read-tree", "--empty"])
        assert code == 0
        assert Repository(str(repo.worktree)).index.paths() == []

    def test_mktree_z_and_missing(self, tmp_path: Path, monkeypatch, capsys) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        monkeypatch.chdir(repo.worktree)
        capsys.readouterr()
        missing = "b" * 64
        monkeypatch.setattr(
            "sys.stdin",
            io.StringIO(f"100644 blob {missing}\tghost.txt\x00"),
        )
        assert dispatch(["mktree", "-z", "--missing"]) == 0
        oid = capsys.readouterr().out.strip()
        assert isinstance(repo.store.read(oid), TreeObject)
