"""Phase 161 tests: explicit ls-files exclude sources."""

from __future__ import annotations

from pathlib import Path

import pytest

from pygit import Repository
from pygit.entrypoint import dispatch


def _repo(tmp_path: Path) -> Repository:
    repo = Repository.init(str(tmp_path / "r"))
    (repo.worktree / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    repo.add(["tracked.txt"])
    return repo


def test_exclude_patterns_filter_others_recursively(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path)
    (repo.worktree / "keep.txt").write_text("keep\n", encoding="utf-8")
    (repo.worktree / "scratch.tmp").write_text("scratch\n", encoding="utf-8")
    (repo.worktree / "nested").mkdir()
    (repo.worktree / "nested" / "cache.log").write_text("cache\n", encoding="utf-8")
    monkeypatch.chdir(repo.worktree)
    capsys.readouterr()

    assert dispatch([
        "ls-files",
        "--others",
        "-x",
        "*.tmp",
        "--exclude=*.log",
    ]) == 0
    assert capsys.readouterr().out == "keep.txt\n"


def test_ignored_accepts_explicit_exclude_without_standard_rules(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path)
    (repo.worktree / "keep.txt").write_text("keep\n", encoding="utf-8")
    (repo.worktree / "scratch.tmp").write_text("scratch\n", encoding="utf-8")
    monkeypatch.chdir(repo.worktree)
    capsys.readouterr()

    assert dispatch(["ls-files", "--others", "--ignored", "-x", "*.tmp"]) == 0
    assert capsys.readouterr().out == "scratch.tmp\n"


def test_exclude_from_reads_multiple_patterns_and_comments(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path)
    (repo.worktree / "keep.txt").write_text("keep\n", encoding="utf-8")
    (repo.worktree / "scratch.tmp").write_text("scratch\n", encoding="utf-8")
    (repo.worktree / "nested").mkdir()
    (repo.worktree / "nested" / "cache.log").write_text("cache\n", encoding="utf-8")
    rules = tmp_path / "rules.exclude"
    rules.write_text("# generated files\n*.tmp\nnested/\n\n", encoding="utf-8")
    monkeypatch.chdir(repo.worktree)
    capsys.readouterr()

    assert dispatch(["ls-files", "--others", "-X", str(rules)]) == 0
    assert capsys.readouterr().out == "keep.txt\n"


def test_directory_does_not_hide_partial_explicit_excludes(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path)
    build = repo.worktree / "build"
    build.mkdir()
    (build / "keep.txt").write_text("keep\n", encoding="utf-8")
    (build / "drop.tmp").write_text("drop\n", encoding="utf-8")
    monkeypatch.chdir(repo.worktree)
    capsys.readouterr()

    assert dispatch(["ls-files", "--others", "--directory", "-x", "*.tmp"]) == 0
    assert capsys.readouterr().out == "build/keep.txt\n"


def test_ignored_directory_collapses_directly_excluded_tree(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path)
    build = repo.worktree / "build"
    build.mkdir()
    (build / "artifact.bin").write_bytes(b"artifact")
    monkeypatch.chdir(repo.worktree)
    capsys.readouterr()

    assert dispatch([
        "ls-files",
        "--others",
        "--ignored",
        "--directory",
        "-x",
        "build/",
    ]) == 0
    assert capsys.readouterr().out == "build/\n"


def test_explicit_exclude_options_require_others(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo.worktree)
    capsys.readouterr()

    with pytest.raises(SystemExit) as exc_info:
        dispatch(["ls-files", "-x", "*.tmp"])
    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "--others" in captured.err


def test_exclude_from_reports_unreadable_file(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo.worktree)
    capsys.readouterr()

    with pytest.raises(SystemExit) as exc_info:
        dispatch(["ls-files", "--others", "-X", "missing.rules"])
    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "cannot read exclude file" in captured.err
