"""Phase 157 tests: ls-files killed file/directory obstructions."""

from __future__ import annotations

import shutil
from pathlib import Path

from pygit import Repository
from pygit.entrypoint import dispatch
from pygit.ls_files_killed import killed_files


def _repo(tmp_path: Path) -> Repository:
    repo = Repository.init(str(tmp_path / "r"))
    (repo.worktree / "dir").mkdir()
    (repo.worktree / "dir" / "file.txt").write_text("tracked\n", encoding="utf-8")
    repo.add(["dir/file.txt"])
    return repo


def test_untracked_file_obstructing_tracked_parent_directory_is_killed(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    shutil.rmtree(repo.worktree / "dir")
    (repo.worktree / "dir").write_text("obstruction\n", encoding="utf-8")
    assert killed_files(repo) == ["dir"]


def test_untracked_descendants_under_tracked_file_path_are_killed(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    target = repo.worktree / "dir" / "file.txt"
    target.unlink()
    target.mkdir()
    (target / "a.txt").write_text("a\n", encoding="utf-8")
    (target / "b.txt").write_text("b\n", encoding="utf-8")
    assert killed_files(repo) == ["dir/file.txt/a.txt", "dir/file.txt/b.txt"]


def test_killed_ignores_unrelated_untracked_and_ignore_rules(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    shutil.rmtree(repo.worktree / "dir")
    (repo.worktree / "dir").write_text("obstruction\n", encoding="utf-8")
    (repo.worktree / ".gitignore").write_text("dir\n", encoding="utf-8")
    (repo.worktree / "plain.txt").write_text("plain\n", encoding="utf-8")
    assert killed_files(repo) == ["dir"]


def test_killed_path_filter_applies_to_emitted_obstruction(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    shutil.rmtree(repo.worktree / "dir")
    (repo.worktree / "dir").write_text("obstruction\n", encoding="utf-8")
    assert killed_files(repo, patterns=["dir"]) == ["dir"]
    assert killed_files(repo, patterns=["dir/file.txt"]) == []


def test_cli_killed_combines_with_cached_and_nul(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo.worktree)
    capsys.readouterr()
    shutil.rmtree(repo.worktree / "dir")
    (repo.worktree / "dir").write_text("obstruction\n", encoding="utf-8")

    assert dispatch(["ls-files", "--killed"]) == 0
    assert capsys.readouterr().out == "dir\n"

    assert dispatch(["ls-files", "--cached", "--killed", "-z"]) == 0
    assert capsys.readouterr().out.split("\x00")[:-1] == ["dir/file.txt", "dir"]
