"""Phase 158 tests: ls-files directory collapsing."""

from __future__ import annotations

from pathlib import Path

from pygit import Repository
from pygit.entrypoint import dispatch
from pygit.ls_files_others import other_files


def _repo(tmp_path: Path) -> Repository:
    repo = Repository.init(str(tmp_path / "r"))
    tracked = repo.worktree / "tracked.txt"
    tracked.write_text("tracked\n", encoding="utf-8")
    repo.add(["tracked.txt"])
    return repo


def test_directory_collapses_wholly_untracked_tree(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo.worktree / "vendor" / "nested").mkdir(parents=True)
    (repo.worktree / "vendor" / "a.txt").write_text("a\n", encoding="utf-8")
    (repo.worktree / "vendor" / "nested" / "b.txt").write_text("b\n", encoding="utf-8")

    assert other_files(repo, directory=True) == ["vendor/"]


def test_directory_does_not_collapse_tree_with_tracked_descendant(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo.worktree / "mixed").mkdir()
    (repo.worktree / "mixed" / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    repo.add(["mixed/tracked.txt"])
    (repo.worktree / "mixed" / "new.txt").write_text("new\n", encoding="utf-8")
    (repo.worktree / "loose").mkdir()
    (repo.worktree / "loose" / "x.txt").write_text("x\n", encoding="utf-8")

    assert other_files(repo, directory=True) == ["loose/", "mixed/new.txt"]


def test_no_empty_directory_suppresses_empty_only_trees(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo.worktree / "empty" / "nested").mkdir(parents=True)
    (repo.worktree / "full").mkdir()
    (repo.worktree / "full" / "x.txt").write_text("x\n", encoding="utf-8")

    assert other_files(repo, directory=True) == ["empty/", "full/"]
    assert other_files(repo, directory=True, no_empty_directory=True) == ["full/"]


def test_narrow_path_pattern_prevents_parent_collapse(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo.worktree / "docs").mkdir()
    (repo.worktree / "docs" / "a.md").write_text("a\n", encoding="utf-8")
    (repo.worktree / "docs" / "b.md").write_text("b\n", encoding="utf-8")

    assert other_files(repo, directory=True, patterns=["docs"]) == ["docs/"]
    assert other_files(repo, directory=True, patterns=["docs/a.md"]) == ["docs/a.md"]


def test_directory_respects_standard_ignore_class(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo.worktree / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    (repo.worktree / "ignored").mkdir()
    (repo.worktree / "ignored" / "artifact.bin").write_bytes(b"x")
    (repo.worktree / "visible").mkdir()
    (repo.worktree / "visible" / "artifact.bin").write_bytes(b"x")

    assert other_files(repo, directory=True, exclude_standard=True) == [".gitignore", "visible/"]
    assert other_files(
        repo,
        directory=True,
        ignored=True,
        exclude_standard=True,
    ) == ["ignored/"]


def test_cli_directory_and_no_empty_directory(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo.worktree)
    capsys.readouterr()
    (repo.worktree / "empty").mkdir()
    (repo.worktree / "src" / "generated").mkdir(parents=True)
    (repo.worktree / "src" / "generated" / "a.py").write_text("pass\n", encoding="utf-8")

    assert dispatch(["ls-files", "--others", "--directory", "--no-empty-directory"]) == 0
    assert capsys.readouterr().out == "src/\n"
