from __future__ import annotations

import pytest

import pygit.fetch_cli as fetch_cli
from pygit.fetch_configured import _apply_destinations
from pygit.fetch_porcelain import _update_destination
from pygit.objects import BlobObject
from pygit.repo import Repository


def _result(remote="origin"):
    return {
        "remote": remote,
        "default_branch": None,
        "refs": {},
        "objects": 0,
        "pruned": [],
        "tag_mode": "auto",
    }


def test_global_force_overrides_tag_clobber_safety(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    old = repo.store.write(BlobObject(b"old"))
    new = repo.store.write(BlobObject(b"new"))
    repo.refs.set_tag("v1", old)

    with pytest.raises(RuntimeError, match="clobber"):
        _update_destination(repo, "refs/tags/v1", new, force=False)

    _update_destination(repo, "refs/tags/v1", new, force=True)
    assert repo.refs.get_tag("v1") == new


def test_configured_global_force_overrides_refspec_force_bit(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    old = repo.store.write(BlobObject(b"old"))
    new = repo.store.write(BlobObject(b"new"))
    repo.refs.set_tag("v1", old)
    destinations = {"refs/tags/v1": [("refs/tags/v1", False)]}

    with pytest.raises(RuntimeError, match="clobber"):
        _apply_destinations(repo, {"refs/tags/v1": new}, destinations)

    _apply_destinations(repo, {"refs/tags/v1": new}, destinations, force=True)
    assert repo.refs.get_tag("v1") == new


def test_force_never_allows_non_commit_branch_target(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    blob = repo.store.write(BlobObject(b"not a commit"))

    with pytest.raises(RuntimeError, match="not a commit"):
        _update_destination(repo, "refs/heads/unsafe", blob, force=True)
    assert repo.refs.get_branch("unsafe") is None


def test_cli_force_forwards_to_configured_fetch(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.invalid/repo.git")
    monkeypatch.chdir(repo.worktree)
    seen = {}

    def fake_configured(*args, **kwargs):
        seen.update(kwargs)
        return _result()

    monkeypatch.setattr(fetch_cli, "fetch_configured", fake_configured)
    monkeypatch.setattr(fetch_cli, "_write_configured_fetch_head", lambda *args: None)
    assert fetch_cli.run_fetch(["--force", "origin"]) == 0
    assert seen["force"] is True


def test_cli_force_forwards_to_explicit_and_direct_fetch(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.invalid/repo.git")
    monkeypatch.chdir(repo.worktree)

    explicit = {}
    direct = {}

    def fake_porcelain(*args, **kwargs):
        explicit.update(kwargs)
        return _result()

    def fake_direct(*args, **kwargs):
        direct.update(kwargs)
        return _result("https://example.invalid/repo.git")

    monkeypatch.setattr(fetch_cli, "fetch_porcelain", fake_porcelain)
    assert fetch_cli.run_fetch(["-f", "origin", "main:peek"]) == 0
    assert explicit["force"] is True

    monkeypatch.setattr(fetch_cli, "fetch_direct_url", fake_direct)
    assert fetch_cli.run_fetch(["-f", "https://example.invalid/repo.git", "main:peek"]) == 0
    assert direct["force"] is True


def test_force_propagates_across_multi_fetch(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("one", "https://example.invalid/one.git")
    repo.add_remote("two", "https://example.invalid/two.git")
    monkeypatch.chdir(repo.worktree)
    seen = []

    def fake_fetch_named(repo_arg, remote, **kwargs):
        seen.append((remote, kwargs["force"]))
        return _result(remote)

    monkeypatch.setattr(fetch_cli, "_fetch_named", fake_fetch_named)
    assert fetch_cli.run_fetch(["--force", "--multiple", "one", "two"]) == 0
    assert seen == [("one", True), ("two", True)]
