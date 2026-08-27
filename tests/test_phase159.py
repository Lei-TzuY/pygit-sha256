"""Phase 159 tests: ls-files subdirectory path semantics."""

from __future__ import annotations

from pathlib import Path

from pygit import Repository
from pygit.entrypoint import dispatch


def _repo(tmp_path: Path) -> Repository:
    repo = Repository.init(str(tmp_path / "r"))
    (repo.worktree / "sub" / "nested").mkdir(parents=True)
    (repo.worktree / "root.txt").write_text("root\n", encoding="utf-8")
    (repo.worktree / "sub" / "a.txt").write_text("a\n", encoding="utf-8")
    (repo.worktree / "sub" / "nested" / "b.txt").write_text("b\n", encoding="utf-8")
    repo.add(["root.txt", "sub/a.txt", "sub/nested/b.txt"])
    return repo


def test_ls_files_from_subdirectory_is_scoped_and_relative(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo.worktree / "sub")
    capsys.readouterr()

    assert dispatch(["ls-files"]) == 0
    assert capsys.readouterr().out == "a.txt\nnested/b.txt\n"


def test_full_name_keeps_subdirectory_scope_but_uses_root_paths(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo.worktree / "sub")
    capsys.readouterr()

    assert dispatch(["ls-files", "--full-name"]) == 0
    assert capsys.readouterr().out == "sub/a.txt\nsub/nested/b.txt\n"


def test_subdirectory_stage_output_rewrites_only_path_field(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path)
    entry = repo.index.entries["sub/a.txt"]
    monkeypatch.chdir(repo.worktree / "sub")
    capsys.readouterr()

    assert dispatch(["ls-files", "--stage", "a.txt"]) == 0
    assert capsys.readouterr().out == f"{entry.mode} {entry.sha} 0\ta.txt\n"


def test_subdirectory_pathspec_can_reach_parent(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo.worktree / "sub")
    capsys.readouterr()

    assert dispatch(["ls-files", "../root.txt"]) == 0
    assert capsys.readouterr().out == "root.txt\n"


def test_others_directory_from_subdirectory_uses_dot_record(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path)
    (repo.worktree / "scratch" / "nested").mkdir(parents=True)
    (repo.worktree / "scratch" / "nested" / "x.txt").write_text("x\n", encoding="utf-8")
    monkeypatch.chdir(repo.worktree / "scratch")
    capsys.readouterr()

    assert dispatch(["ls-files", "--others", "--directory"]) == 0
    assert capsys.readouterr().out == "./\n"

    assert dispatch(["ls-files", "--others", "--directory", "--full-name"]) == 0
    assert capsys.readouterr().out == "scratch/\n"


def test_subdirectory_nul_output_uses_relative_names(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo.worktree / "sub")
    capsys.readouterr()

    assert dispatch(["ls-files", "-z"]) == 0
    assert capsys.readouterr().out == "a.txt\x00nested/b.txt\x00"
