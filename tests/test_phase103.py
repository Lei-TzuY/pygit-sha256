"""Phase 103 regression coverage for strict commit-graph verification."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from pygit import Repository
from pygit.application import main as application_main
from pygit.commit_graph import CommitGraph, CommitGraphError


def _commit_file(repo: Repository, name: str, content: str, message: str) -> str:
    path = repo.worktree / name
    path.write_text(content, encoding="utf-8")
    repo.add([name])
    return repo.commit(message, author_name="Tester", author_email="t@example.com")


def _two_commit_repo(tmp_path: Path) -> tuple[Repository, str, str]:
    repo = Repository.init(str(tmp_path / "repo"))
    first = _commit_file(repo, "a.txt", "one\n", "first")
    second = _commit_file(repo, "a.txt", "two\n", "second")
    return repo, first, second


def test_commit_graph_round_trip_and_store_verification(tmp_path: Path) -> None:
    repo, first, second = _two_commit_repo(tmp_path)
    path = repo.write_commit_graph()

    graph = CommitGraph(repo.pygit_dir)
    entries = graph.read()
    result = graph.verify(repo.store)

    assert path == result.path
    assert result.commit_count == 2
    assert result.max_generation == 2
    assert entries[second][1] == [first]
    assert not any(
        candidate.name.startswith(".commit-graph.")
        for candidate in path.parent.iterdir()
    )


def test_commit_graph_rejects_bad_header_version_and_trailing_bytes(
    tmp_path: Path,
) -> None:
    repo, _, _ = _two_commit_repo(tmp_path)
    path = repo.write_commit_graph()
    original = path.read_bytes()
    graph = CommitGraph(repo.pygit_dir)

    path.write_bytes(b"NOPE" + original[4:])
    with pytest.raises(CommitGraphError, match="signature"):
        graph.read()

    bad_version = bytearray(original)
    bad_version[4] = 2
    path.write_bytes(bytes(bad_version))
    with pytest.raises(CommitGraphError, match="version 2"):
        graph.read()

    path.write_bytes(original + b"x")
    with pytest.raises(CommitGraphError, match="trailing byte"):
        graph.read()


def test_commit_graph_rejects_truncation_and_invalid_generation(tmp_path: Path) -> None:
    repo, _, _ = _two_commit_repo(tmp_path)
    path = repo.write_commit_graph()
    original = path.read_bytes()
    graph = CommitGraph(repo.pygit_dir)

    path.write_bytes(original[:-1])
    with pytest.raises(CommitGraphError, match="truncated"):
        graph.read()

    bad_generation = bytearray(original)
    # Header (10) + commit id (32) + tree id (32) reaches the first
    # entry's generation field.
    bad_generation[74:78] = b"\x00\x00\x00\x00"
    path.write_bytes(bytes(bad_generation))
    with pytest.raises(CommitGraphError, match="generation"):
        graph.read()


def test_commit_graph_verify_detects_object_metadata_mismatch(tmp_path: Path) -> None:
    repo, _, _ = _two_commit_repo(tmp_path)
    path = repo.write_commit_graph()
    data = bytearray(path.read_bytes())

    # Corrupt only the first entry's stored tree id. Structural parsing still
    # succeeds; repository-aware verification must reject the stale metadata.
    data[42] ^= 0x01
    path.write_bytes(bytes(data))

    with pytest.raises(CommitGraphError, match="tree mismatch"):
        CommitGraph(repo.pygit_dir).verify(repo.store)


def test_commit_graph_writer_rejects_cycles_and_duplicate_ids(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    graph = CommitGraph(repo.pygit_dir)
    a = "11" * 32
    b = "22" * 32
    tree = "33" * 32

    with pytest.raises(CommitGraphError, match="cycle"):
        graph.write([(a, tree, [b]), (b, tree, [a])])

    with pytest.raises(CommitGraphError, match="duplicate"):
        graph.write([(a, tree, []), (a, tree, [])])


def test_commit_graph_modern_cli_write_verify_and_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, _, _ = _two_commit_repo(tmp_path)
    monkeypatch.chdir(repo.worktree)

    monkeypatch.setattr(sys, "argv", ["pygit", "commit-graph", "write"])
    application_main()
    write_out = capsys.readouterr().out
    assert "Wrote commit-graph" in write_out

    monkeypatch.setattr(sys, "argv", ["pygit", "commit-graph", "verify"])
    application_main()
    verify_out = capsys.readouterr().out
    assert ": ok (2 commits, max generation 2)" in verify_out

    path = repo.pygit_dir / "objects" / "info" / "commit-graph"
    path.write_bytes(path.read_bytes() + b"garbage")
    with pytest.raises(SystemExit) as exc_info:
        application_main()
    captured = capsys.readouterr()
    assert exc_info.value.code == 1
    assert "trailing byte" in captured.err
