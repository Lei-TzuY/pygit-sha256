from __future__ import annotations

from pathlib import Path

import pytest

import pygit.fetch_cli_dry_run as dry_cli
from pygit.fetch_dry_run import dry_run_repository
from pygit.objects import BlobObject
from pygit.repo import Repository


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_dry_run_repository_restores_all_pygit_mutations(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    original = repo.store.write(BlobObject(b"original"))
    repo.refs.set_tag("keep", original)
    before = _snapshot(Path(repo.pygit_dir))

    with dry_run_repository(repo):
        transient = repo.store.write(BlobObject(b"transient"))
        repo.refs.set_tag("keep", transient)
        repo.refs.set_tag("new", transient)
        Path(repo.pygit_dir, "FETCH_HEAD").write_text(f"{transient}\t\tbranch 'main'\n")
        Path(repo.pygit_dir, "phase192-marker").write_text("temporary")

    assert _snapshot(Path(repo.pygit_dir)) == before
    assert repo.refs.get_tag("keep") == original
    assert repo.refs.get_tag("new") is None


def test_dry_run_repository_restores_after_failure(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    before = _snapshot(Path(repo.pygit_dir))

    with pytest.raises(RuntimeError, match="boom"):
        with dry_run_repository(repo):
            Path(repo.pygit_dir, "temporary").write_text("changed")
            raise RuntimeError("boom")

    assert _snapshot(Path(repo.pygit_dir)) == before


def test_cli_dry_run_uses_real_fetch_path_but_disables_fetch_head(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.invalid/repo.git")
    monkeypatch.chdir(repo.worktree)
    seen = []

    def fake_run(argv):
        seen.extend(argv)
        Path(repo.pygit_dir, "temporary").write_text("changed")
        return 0

    monkeypatch.setattr(dry_cli, "_run_fetch", fake_run)
    before = _snapshot(Path(repo.pygit_dir))

    assert dry_cli.run_fetch(["--dry-run", "--write-fetch-head", "origin"]) == 0
    assert "--dry-run" not in seen
    assert "--write-fetch-head" not in seen
    assert "--no-write-fetch-head" in seen
    assert _snapshot(Path(repo.pygit_dir)) == before


def test_cli_without_dry_run_is_transparent(monkeypatch):
    seen = []
    monkeypatch.setattr(dry_cli, "_run_fetch", lambda argv: seen.extend(argv) or 7)

    assert dry_cli.run_fetch(["--force", "origin"]) == 7
    assert seen == ["--force", "origin"]


def test_dry_run_composes_with_atomic_option(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    monkeypatch.chdir(repo.worktree)
    seen = []

    monkeypatch.setattr(dry_cli, "_run_fetch", lambda argv: seen.extend(argv) or 0)
    assert dry_cli.run_fetch(["--dry-run", "--atomic", "origin"]) == 0
    assert seen == ["--atomic", "origin", "--no-write-fetch-head"]


def test_dry_run_option_after_separator_is_left_as_refspec(monkeypatch):
    seen = []
    monkeypatch.setattr(dry_cli, "_run_fetch", lambda argv: seen.extend(argv) or 0)

    assert dry_cli.run_fetch(["origin", "--", "--dry-run"]) == 0
    assert seen == ["origin", "--", "--dry-run"]


def test_dry_run_fetch_head_suppression_stays_before_separator(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    monkeypatch.chdir(repo.worktree)
    seen = []
    monkeypatch.setattr(dry_cli, "_run_fetch", lambda argv: seen.extend(argv) or 0)

    assert dry_cli.run_fetch(["--dry-run", "origin", "--", "topic"]) == 0
    assert seen == ["origin", "--no-write-fetch-head", "--", "topic"]
