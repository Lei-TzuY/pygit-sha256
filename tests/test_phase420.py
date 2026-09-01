"""Phase 420 tests: Git-style ls-files duplicate preservation and suppression."""

from __future__ import annotations

from pathlib import Path

from pygit import Repository
from pygit.entrypoint import dispatch
from pygit.index_plumbing import update_index
from pygit.objects import BlobObject


def _repo(tmp_path: Path) -> Repository:
    repo = Repository.init(str(tmp_path / "r"))
    (repo.worktree / "a.txt").write_text("a\n", encoding="utf-8")
    (repo.worktree / "b.txt").write_text("b\n", encoding="utf-8")
    repo.add(["a.txt", "b.txt"])
    return repo


def _install_conflict(repo: Repository, path: str = "conflict.txt") -> None:
    base = repo.store.write(BlobObject(b"base\n"))
    ours = repo.store.write(BlobObject(b"ours\n"))
    theirs = repo.store.write(BlobObject(b"theirs\n"))
    zero = "0" * 64
    update_index(
        repo,
        index_info=[
            f"0 {zero}\t{path}",
            f"100644 {base} 1\t{path}",
            f"100644 {ours} 2\t{path}",
            f"100644 {theirs} 3\t{path}",
        ],
    )


def test_filename_selectors_preserve_duplicate_origins(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path)
    (repo.worktree / "a.txt").write_text("changed\n", encoding="utf-8")
    monkeypatch.chdir(repo.worktree)
    capsys.readouterr()

    assert dispatch(["ls-files", "--cached", "--modified"]) == 0
    assert capsys.readouterr().out == "a.txt\na.txt\nb.txt\n"


def test_deduplicate_suppresses_filename_selector_duplicates(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path)
    (repo.worktree / "a.txt").write_text("changed\n", encoding="utf-8")
    monkeypatch.chdir(repo.worktree)
    capsys.readouterr()

    assert dispatch(["ls-files", "--cached", "--modified", "--deduplicate"]) == 0
    assert capsys.readouterr().out == "a.txt\nb.txt\n"


def test_plain_cached_conflict_keeps_one_record_per_stage(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path)
    _install_conflict(repo)
    monkeypatch.chdir(repo.worktree)
    capsys.readouterr()

    assert dispatch(["ls-files", "conflict.txt"]) == 0
    assert capsys.readouterr().out == "conflict.txt\nconflict.txt\nconflict.txt\n"

    assert dispatch(["ls-files", "--deduplicate", "conflict.txt"]) == 0
    assert capsys.readouterr().out == "conflict.txt\n"


def test_deduplicate_has_no_effect_on_stage_output(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path)
    _install_conflict(repo)
    monkeypatch.chdir(repo.worktree)
    capsys.readouterr()

    assert dispatch(["ls-files", "--stage", "conflict.txt"]) == 0
    plain = capsys.readouterr().out
    assert dispatch(["ls-files", "--stage", "--deduplicate", "conflict.txt"]) == 0
    deduplicated = capsys.readouterr().out

    assert deduplicated == plain
    assert len(plain.splitlines()) == 3
    assert " 1\tconflict.txt" in plain
    assert " 2\tconflict.txt" in plain
    assert " 3\tconflict.txt" in plain


def test_deduplicate_has_no_effect_on_unmerged_output(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path)
    _install_conflict(repo)
    monkeypatch.chdir(repo.worktree)
    capsys.readouterr()

    assert dispatch(["ls-files", "--unmerged", "conflict.txt"]) == 0
    plain = capsys.readouterr().out
    assert dispatch(["ls-files", "--unmerged", "--deduplicate", "conflict.txt"]) == 0
    deduplicated = capsys.readouterr().out

    assert deduplicated == plain
    assert len(plain.splitlines()) == 3


def test_nul_framing_preserves_or_suppresses_duplicates(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path)
    (repo.worktree / "a.txt").write_text("changed\n", encoding="utf-8")
    monkeypatch.chdir(repo.worktree)
    capsys.readouterr()

    assert dispatch(["ls-files", "-z", "--cached", "--modified", "a.txt"]) == 0
    assert capsys.readouterr().out == "a.txt\x00a.txt\x00"

    assert dispatch([
        "ls-files",
        "-z",
        "--cached",
        "--modified",
        "--deduplicate",
        "a.txt",
    ]) == 0
    assert capsys.readouterr().out == "a.txt\x00"


def test_help_lists_deduplicate_option(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo.worktree)
    capsys.readouterr()

    try:
        dispatch(["ls-files", "--help"])
    except SystemExit as exc:
        assert exc.code == 0
    assert "--deduplicate" in capsys.readouterr().out
