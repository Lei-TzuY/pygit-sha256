"""Phase 60 tests: side-effect-free ``merge-tree`` plumbing."""

from __future__ import annotations

from pathlib import Path

from pygit import Repository
from pygit.launcher import _run_merge_tree
from pygit.merge_tree import merge_tree
from pygit.objects import BlobObject
from pygit.tree_plumbing import flatten_tree


def _commit_file(repo: Repository, content: str, message: str) -> str:
    path = repo.worktree / "f.txt"
    path.write_text(content, encoding="utf-8")
    repo.add(["f.txt"])
    return repo.commit(message, author_name="Tester", author_email="t@example.com")


def _diverged_repo(tmp_path: Path, ours: str, theirs: str) -> Repository:
    repo = Repository.init(str(tmp_path / "r"))
    _commit_file(repo, "one\ntwo\nthree\n", "base")
    repo.branch("ours")
    repo.branch("theirs")

    repo.checkout("ours")
    _commit_file(repo, ours, "ours")
    repo.checkout("theirs")
    _commit_file(repo, theirs, "theirs")
    repo.checkout("main")
    return repo


def test_merge_tree_combines_non_overlapping_line_edits_without_state_changes(tmp_path: Path) -> None:
    repo = _diverged_repo(tmp_path, "ONE\ntwo\nthree\n", "one\ntwo\nTHREE\n")
    head_before = repo.refs.resolve_head()
    index_before = (repo.pygit_dir / "index").read_bytes()
    worktree_before = (repo.worktree / "f.txt").read_bytes()

    result = merge_tree(repo, "ours", "theirs")

    assert result.clean
    assert result.tree_oid is not None
    entries = flatten_tree(repo, result.tree_oid)
    blob = repo.store.read(entries["f.txt"][0])
    assert isinstance(blob, BlobObject)
    assert blob.data == b"ONE\ntwo\nTHREE\n"
    assert repo.refs.resolve_head() == head_before
    assert (repo.pygit_dir / "index").read_bytes() == index_before
    assert (repo.worktree / "f.txt").read_bytes() == worktree_before


def test_merge_tree_reports_overlapping_edit_conflict(tmp_path: Path) -> None:
    repo = _diverged_repo(tmp_path, "OURS\ntwo\nthree\n", "THEIRS\ntwo\nthree\n")
    result = merge_tree(repo, "ours", "theirs")
    assert not result.clean
    assert result.tree_oid is None
    assert result.conflicts == ("f.txt",)


def test_merge_tree_reports_delete_modify_conflict(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "r"))
    _commit_file(repo, "base\n", "base")
    repo.branch("delete")
    repo.branch("modify")

    repo.checkout("delete")
    repo.rm("f.txt")
    repo.commit("delete")
    repo.checkout("modify")
    _commit_file(repo, "modified\n", "modify")

    result = merge_tree(repo, "delete", "modify")
    assert result.tree_oid is None
    assert result.conflicts == ("f.txt",)


def test_merge_tree_cli_prints_tree_or_conflicts(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _diverged_repo(tmp_path, "ONE\ntwo\nthree\n", "one\ntwo\nTHREE\n")
    monkeypatch.chdir(repo.worktree)
    assert _run_merge_tree(["--messages", "ours", "theirs"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines[0]) == 64
    assert lines[-1] == "clean"

    repo.checkout("ours")
    _commit_file(repo, "CONFLICT\ntwo\nthree\n", "ours-conflict")
    assert _run_merge_tree(["ours", "theirs"]) == 1
    assert capsys.readouterr().out.strip() == "CONFLICT\tf.txt"
