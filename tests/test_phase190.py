from __future__ import annotations

from pygit.fetch_cli import run_fetch
from pygit.repo import Repository


def _result(remote="origin"):
    return {
        "remote": remote,
        "default_branch": "main",
        "refs": {"refs/heads/main": "a" * 64},
        "objects": 0,
        "pruned": [],
        "tag_mode": "auto",
    }


def test_fetch_no_write_fetch_head_skips_configured_metadata(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.invalid/repo.git")
    monkeypatch.chdir(repo.worktree)
    monkeypatch.setattr("pygit.fetch_cli.fetch_configured", lambda *a, **k: _result())

    writes = []
    monkeypatch.setattr("pygit.fetch_cli._write_configured_fetch_head", lambda *a, **k: writes.append(1))

    assert run_fetch(["--no-write-fetch-head", "origin"]) == 0
    assert writes == []


def test_fetch_write_fetch_head_is_default_and_can_be_reenabled(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.invalid/repo.git")
    monkeypatch.chdir(repo.worktree)
    monkeypatch.setattr("pygit.fetch_cli.fetch_configured", lambda *a, **k: _result())

    writes = []
    monkeypatch.setattr("pygit.fetch_cli._write_configured_fetch_head", lambda *a, **k: writes.append(1))

    assert run_fetch(["origin"]) == 0
    assert run_fetch(["--write-fetch-head", "origin"]) == 0
    assert writes == [1, 1]


def test_no_write_fetch_head_forwards_to_explicit_porcelain(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.invalid/repo.git")
    monkeypatch.chdir(repo.worktree)

    seen = {}
    def fake_porcelain(*args, **kwargs):
        seen.update(kwargs)
        return _result()
    monkeypatch.setattr("pygit.fetch_cli.fetch_porcelain", fake_porcelain)

    assert run_fetch(["--no-write-fetch-head", "origin", "main"]) == 0
    assert seen["write_fetch_head"] is False


def test_no_write_fetch_head_forwards_to_direct_url(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    monkeypatch.chdir(repo.worktree)

    seen = {}
    def fake_direct(*args, **kwargs):
        seen.update(kwargs)
        return _result("https://example.invalid/repo.git")
    monkeypatch.setattr("pygit.fetch_cli.fetch_direct_url", fake_direct)

    assert run_fetch(["--no-write-fetch-head", "https://example.invalid/repo.git"]) == 0
    assert seen["write_fetch_head"] is False


def test_no_write_fetch_head_propagates_across_multi_fetch(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("one", "https://example.invalid/one.git")
    repo.add_remote("two", "https://example.invalid/two.git")
    monkeypatch.chdir(repo.worktree)

    seen = []
    def fake_fetch_named(repo_arg, remote, **kwargs):
        seen.append((remote, kwargs["write_fetch_head_enabled"]))
        return _result(remote)
    monkeypatch.setattr("pygit.fetch_cli._fetch_named", fake_fetch_named)

    assert run_fetch(["--no-write-fetch-head", "--multiple", "one", "two"]) == 0
    assert seen == [("one", False), ("two", False)]


def test_append_with_no_write_fetch_head_does_not_force_metadata_write(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.invalid/repo.git")
    monkeypatch.chdir(repo.worktree)

    seen = {}
    def fake_porcelain(*args, **kwargs):
        seen.update(kwargs)
        return _result()
    monkeypatch.setattr("pygit.fetch_cli.fetch_porcelain", fake_porcelain)

    assert run_fetch(["--append", "--no-write-fetch-head", "origin"]) == 0
    assert seen["append_fetch_head"] is True
    assert seen["write_fetch_head"] is False
