"""Phase 160 tests: selector-aware ls-files --error-unmatch."""

from __future__ import annotations

from pathlib import Path

from pygit import Repository
from pygit.entrypoint import dispatch


def _repo(tmp_path: Path) -> Repository:
    repo = Repository.init(str(tmp_path / "r"))
    (repo.worktree / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    (repo.worktree / "nested").mkdir()
    (repo.worktree / "nested" / "file.txt").write_text("nested\n", encoding="utf-8")
    repo.add(["tracked.txt", "nested/file.txt"])
    return repo


def test_error_unmatch_uses_selected_deleted_records(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo.worktree)
    capsys.readouterr()

    assert dispatch(["ls-files", "--deleted", "--error-unmatch", "tracked.txt"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "tracked.txt" in captured.err

    (repo.worktree / "tracked.txt").unlink()
    assert dispatch(["ls-files", "--deleted", "--error-unmatch", "tracked.txt"]) == 0
    assert capsys.readouterr().out == "tracked.txt\n"


def test_error_unmatch_supports_others(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path)
    (repo.worktree / "other.txt").write_text("other\n", encoding="utf-8")
    monkeypatch.chdir(repo.worktree)
    capsys.readouterr()

    assert dispatch(["ls-files", "--others", "--error-unmatch", "other.txt"]) == 0
    assert capsys.readouterr().out == "other.txt\n"

    assert dispatch(["ls-files", "--others", "--error-unmatch", "missing.txt"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "missing.txt" in captured.err


def test_error_unmatch_combines_index_and_worktree_selectors(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path)
    (repo.worktree / "other.txt").write_text("other\n", encoding="utf-8")
    monkeypatch.chdir(repo.worktree)
    capsys.readouterr()

    assert dispatch([
        "ls-files",
        "--cached",
        "--others",
        "--error-unmatch",
        "tracked.txt",
        "other.txt",
    ]) == 0
    assert capsys.readouterr().out == "tracked.txt\nother.txt\n"


def test_error_unmatch_supports_killed_paths(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path)
    tracked = repo.worktree / "nested" / "file.txt"
    tracked.unlink()
    (repo.worktree / "nested").rmdir()
    (repo.worktree / "nested").write_text("obstruction\n", encoding="utf-8")
    monkeypatch.chdir(repo.worktree)
    capsys.readouterr()

    assert dispatch(["ls-files", "--killed", "--error-unmatch", "nested"]) == 0
    assert capsys.readouterr().out == "nested\n"


def test_error_unmatch_honors_subdirectory_scope(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo.worktree / "nested")
    capsys.readouterr()

    assert dispatch(["ls-files", "--error-unmatch", "file.txt"]) == 0
    assert capsys.readouterr().out == "file.txt\n"

    assert dispatch(["ls-files", "--error-unmatch", "missing.txt"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "nested/missing.txt" in captured.err
