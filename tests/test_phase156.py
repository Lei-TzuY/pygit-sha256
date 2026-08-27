"""Phase 156 tests: ls-files worktree and ignore selectors."""

from __future__ import annotations

from pathlib import Path

import pytest

from pygit import Repository
from pygit.entrypoint import dispatch
from pygit.ls_files_others import other_files


def _repo(tmp_path: Path) -> Repository:
    repo = Repository.init(str(tmp_path / "r"))
    tracked = repo.worktree / "tracked.txt"
    tracked.write_text("tracked\n", encoding="utf-8")
    repo.add(["tracked.txt"])
    return repo


def test_other_files_excludes_index_and_internal_metadata(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo.worktree / "plain.txt").write_text("plain\n", encoding="utf-8")
    (repo.worktree / "nested").mkdir()
    (repo.worktree / "nested" / "new.txt").write_text("nested\n", encoding="utf-8")

    assert other_files(repo) == ["nested/new.txt", "plain.txt"]


def test_exclude_standard_and_ignored_are_complementary(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo.worktree / ".gitignore").write_text("*.log\nbuild/\n", encoding="utf-8")
    (repo.worktree / "keep.txt").write_text("keep\n", encoding="utf-8")
    (repo.worktree / "debug.log").write_text("ignored\n", encoding="utf-8")
    (repo.worktree / "build").mkdir()
    (repo.worktree / "build" / "artifact.bin").write_bytes(b"artifact")

    visible = other_files(repo, exclude_standard=True)
    assert visible == [".gitignore", "keep.txt"]

    ignored = other_files(repo, ignored=True, exclude_standard=True)
    assert ignored == ["build/artifact.bin", "debug.log"]


def test_other_files_honors_path_patterns(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo.worktree / "docs").mkdir()
    (repo.worktree / "docs" / "a.md").write_text("a\n", encoding="utf-8")
    (repo.worktree / "docs" / "b.txt").write_text("b\n", encoding="utf-8")
    (repo.worktree / "root.md").write_text("root\n", encoding="utf-8")

    assert other_files(repo, patterns=["docs/"]) == ["docs/a.md", "docs/b.txt"]
    assert other_files(repo, patterns=["*.md"]) == ["docs/a.md", "root.md"]


def test_ignored_requires_standard_excludes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    with pytest.raises(ValueError, match="exclude-standard"):
        other_files(repo, ignored=True)


def test_cli_others_exclude_standard_and_nul(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo.worktree)
    (repo.worktree / ".pygitignore").write_text("*.tmp\n", encoding="utf-8")
    (repo.worktree / "plain.txt").write_text("plain\n", encoding="utf-8")
    (repo.worktree / "scratch.tmp").write_text("tmp\n", encoding="utf-8")

    assert dispatch(["ls-files", "--others", "--exclude-standard", "-z"]) == 0
    output = capsys.readouterr().out
    assert output.endswith("\x00")
    assert output.split("\x00")[:-1] == [".pygitignore", "plain.txt"]

    assert dispatch(
        ["ls-files", "--others", "--ignored", "--exclude-standard"]
    ) == 0
    assert capsys.readouterr().out == "scratch.tmp\n"


def test_cli_can_union_cached_and_other_paths(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo.worktree)
    (repo.worktree / "new.txt").write_text("new\n", encoding="utf-8")

    assert dispatch(["ls-files", "--cached", "--others"]) == 0
    assert capsys.readouterr().out.splitlines() == ["new.txt", "tracked.txt"]
