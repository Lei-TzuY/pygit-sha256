"""Phase 60 tests: tree-only three-way merge plumbing."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Mapping, Sequence

import pytest

from pygit import Repository, merge_tree
from pygit.launcher import _run_merge_tree
from pygit.objects import BlobObject, CommitObject, Identity, TreeEntry, TreeObject
from pygit.tree_plumbing import flatten_tree


FileMap = Mapping[str, bytes]


def _write_tree(repo: Repository, files: FileMap) -> str:
    root: Dict[str, object] = {}
    for path, data in sorted(files.items()):
        parts = path.split("/")
        node = root
        for part in parts[:-1]:
            child = node.setdefault(part, {})
            assert isinstance(child, dict)
            node = child
        node[parts[-1]] = repo.store.write(BlobObject(data))

    def build(node: Mapping[str, object]) -> str:
        entries = []
        for name in sorted(node):
            value = node[name]
            if isinstance(value, dict):
                entries.append(TreeEntry("040000", name, build(value)))
            else:
                entries.append(TreeEntry("100644", name, str(value)))
        return repo.store.write(TreeObject(entries))

    return build(root)


def _commit(
    repo: Repository,
    files: FileMap,
    *,
    parents: Sequence[str] = (),
    message: str = "commit",
) -> str:
    ident = Identity("Tester", "t@example.com", timestamp=1, timezone="+0000")
    return repo.store.write(
        CommitObject(
            tree=_write_tree(repo, files),
            parents=list(parents),
            author=ident,
            committer=ident,
            message=message,
        )
    )


def _blob_at(repo: Repository, tree_oid: str, path: str) -> bytes:
    entry = flatten_tree(repo, tree_oid)[path]
    obj = repo.store.read(entry[0])
    assert isinstance(obj, BlobObject)
    return obj.data


def test_merge_tree_combines_non_overlapping_edits_without_state_changes(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "r"))
    base = _commit(repo, {"f.txt": b"one\ntwo\nthree\n"}, message="base")
    ours = _commit(repo, {"f.txt": b"ONE\ntwo\nthree\n"}, parents=[base], message="ours")
    theirs = _commit(repo, {"f.txt": b"one\ntwo\nTHREE\n"}, parents=[base], message="theirs")

    repo.refs.set_branch("main", base)
    sentinel = repo.worktree / "sentinel.txt"
    sentinel.write_text("keep\n", encoding="utf-8")
    repo.add(["sentinel.txt"])
    head_before = repo.refs.resolve_head()
    index_before = (repo.pygit_dir / "index").read_bytes()
    worktree_before = sentinel.read_bytes()

    result = merge_tree(repo, ours, theirs)

    assert result.clean
    assert result.base_oid == base
    assert result.tree_oid is not None
    assert _blob_at(repo, result.tree_oid, "f.txt") == b"ONE\ntwo\nTHREE\n"
    assert result.changed_paths == ("f.txt",)
    assert repo.refs.resolve_head() == head_before
    assert (repo.pygit_dir / "index").read_bytes() == index_before
    assert sentinel.read_bytes() == worktree_before


def test_overlapping_edit_returns_structured_content_conflict(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "r"))
    base = _commit(repo, {"f.txt": b"base\n"}, message="base")
    ours = _commit(repo, {"f.txt": b"ours\n"}, parents=[base], message="ours")
    theirs = _commit(repo, {"f.txt": b"theirs\n"}, parents=[base], message="theirs")
    objects_before = set(repo.store.all_shas())

    result = merge_tree(repo, ours, theirs)

    assert not result.clean
    assert result.tree_oid is None
    assert len(result.conflicts) == 1
    conflict = result.conflicts[0]
    assert conflict.path == "f.txt"
    assert conflict.reason == "content"
    assert conflict.base_oid is not None
    assert conflict.ours_oid is not None
    assert conflict.theirs_oid is not None
    assert set(repo.store.all_shas()) == objects_before


def test_invalid_utf8_changes_are_binary_conflicts_without_lossy_merge(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "r"))
    base = _commit(repo, {"f.bin": b"\xff\none\nthree\n"}, message="base")
    ours = _commit(repo, {"f.bin": b"\xff\nONE\nthree\n"}, parents=[base], message="ours")
    theirs = _commit(repo, {"f.bin": b"\xff\none\nTHREE\n"}, parents=[base], message="theirs")
    objects_before = set(repo.store.all_shas())

    result = merge_tree(repo, ours, theirs)

    assert result.tree_oid is None
    assert [(c.path, c.reason) for c in result.conflicts] == [("f.bin", "binary")]
    assert set(repo.store.all_shas()) == objects_before


def test_add_add_and_modify_delete_are_not_guessed(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "r"))
    base = _commit(repo, {}, message="base")
    ours = _commit(repo, {"new.txt": b"ours\n"}, parents=[base], message="ours")
    theirs = _commit(repo, {"new.txt": b"theirs\n"}, parents=[base], message="theirs")
    result = merge_tree(repo, ours, theirs)
    assert [(c.path, c.reason) for c in result.conflicts] == [("new.txt", "add/add")]

    base2 = _commit(repo, {"f.txt": b"base\n"}, message="base2")
    deleted = _commit(repo, {}, parents=[base2], message="delete")
    modified = _commit(repo, {"f.txt": b"modified\n"}, parents=[base2], message="modify")
    result2 = merge_tree(repo, deleted, modified)
    assert [(c.path, c.reason) for c in result2.conflicts] == [("f.txt", "modify/delete")]


def test_directory_file_collision_is_reported_before_tree_write(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "r"))
    base = _commit(repo, {}, message="base")
    ours = _commit(repo, {"node": b"file\n"}, parents=[base], message="ours")
    theirs = _commit(repo, {"node/child.txt": b"child\n"}, parents=[base], message="theirs")

    result = merge_tree(repo, ours, theirs)

    assert result.tree_oid is None
    assert [(c.path, c.reason) for c in result.conflicts] == [("node", "directory/file")]


def test_unrelated_histories_require_opt_in(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "r"))
    ours = _commit(repo, {"ours.txt": b"ours\n"}, message="root-a")
    theirs = _commit(repo, {"theirs.txt": b"theirs\n"}, message="root-b")

    with pytest.raises(RuntimeError, match="unrelated histories"):
        merge_tree(repo, ours, theirs)

    result = merge_tree(repo, ours, theirs, allow_unrelated_histories=True)
    assert result.clean
    assert result.base_oid is None
    assert result.tree_oid is not None
    assert set(flatten_tree(repo, result.tree_oid)) == {"ours.txt", "theirs.txt"}


def test_explicit_merge_base_and_cli_output(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = Repository.init(str(tmp_path / "r"))
    base = _commit(repo, {"f.txt": b"one\ntwo\nthree\n"}, message="base")
    ours = _commit(repo, {"f.txt": b"ONE\ntwo\nthree\n"}, parents=[base], message="ours")
    theirs = _commit(repo, {"f.txt": b"one\ntwo\nTHREE\n"}, parents=[base], message="theirs")
    repo.refs.set_branch("base", base)
    repo.refs.set_branch("ours", ours)
    repo.refs.set_branch("theirs", theirs)
    monkeypatch.chdir(repo.worktree)
    capsys.readouterr()

    result = merge_tree(repo, "ours", "theirs", base="base")
    assert result.base_oid == base

    assert _run_merge_tree(["--write-tree", "--merge-base", "base", "--messages", "ours", "theirs"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines[0]) == 64
    assert lines[1] == f"base {base}"
    assert lines[2] == "clean"


def test_cli_name_only_conflicts_and_unrelated_flag(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = Repository.init(str(tmp_path / "r"))
    base = _commit(repo, {"f.txt": b"base\n"}, message="base")
    ours = _commit(repo, {"f.txt": b"ours\n"}, parents=[base], message="ours")
    theirs = _commit(repo, {"f.txt": b"theirs\n"}, parents=[base], message="theirs")
    repo.refs.set_branch("ours", ours)
    repo.refs.set_branch("theirs", theirs)
    monkeypatch.chdir(repo.worktree)
    capsys.readouterr()

    assert _run_merge_tree(["--name-only", "ours", "theirs"]) == 1
    assert capsys.readouterr().out.strip() == "f.txt"

    root_a = _commit(repo, {"a": b"a"}, message="root-a")
    root_b = _commit(repo, {"b": b"b"}, message="root-b")
    repo.refs.set_branch("root-a", root_a)
    repo.refs.set_branch("root-b", root_b)
    assert _run_merge_tree(["--allow-unrelated-histories", "root-a", "root-b"]) == 0
    assert len(capsys.readouterr().out.strip()) == 64
