from __future__ import annotations

import pytest

from pygit.fetch_cli import run_fetch
from pygit.fetch_porcelain import fetch_porcelain
from pygit.objects import CommitObject
from pygit.remote import Advertisement
from pygit.repo import Repository


def _configured_repo(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")
    repo.config_set("remote", "origin.url", "https://example.test/repo.git")
    repo.config_set(
        "remote",
        "origin.fetch",
        "+refs/heads/*:refs/remotes/origin/*",
    )
    return repo


def _known_client(monkeypatch, refs):
    class Client:
        def __init__(self, url):
            assert url == "https://example.test/repo.git"

        def discover(self):
            return Advertisement(refs, set(), {})

        def fetch(self, *args, **kwargs):
            raise AssertionError("known objects should not require upload-pack")

    monkeypatch.setattr("pygit.fetch_porcelain.SmartHttpClient", Client)


def test_refmap_replaces_configured_destination_mapping(tmp_path, monkeypatch):
    repo = _configured_repo(tmp_path)
    internal, native = "a" * 64, "b" * 40
    repo._write_native_map({internal: native}, "origin")
    _known_client(monkeypatch, {"refs/heads/main": native})

    result = fetch_porcelain(
        repo,
        "origin",
        refspecs=["main"],
        refmap=["+refs/heads/*:refs/remotes/origin/mapped-*"],
        tags=False,
    )

    assert result["refs"] == {"refs/heads/main": internal}
    assert repo.refs.get_remote("origin", "mapped-main") == internal
    assert repo.refs.get_remote("origin", "main") is None


def test_repeated_refmap_values_update_each_mapping(tmp_path, monkeypatch):
    repo = _configured_repo(tmp_path)
    internal, native = "c" * 64, "d" * 40
    repo._write_native_map({internal: native}, "origin")
    _known_client(monkeypatch, {"refs/heads/main": native})

    fetch_porcelain(
        repo,
        "origin",
        refspecs=["main"],
        refmap=[
            "+refs/heads/*:refs/remotes/origin/one-*",
            "+refs/heads/*:refs/remotes/origin/two-*",
        ],
        tags=False,
    )

    assert repo.refs.get_remote("origin", "one-main") == internal
    assert repo.refs.get_remote("origin", "two-main") == internal
    assert repo.refs.get_remote("origin", "main") is None


def test_empty_refmap_disables_configured_destination_mapping(tmp_path, monkeypatch):
    repo = _configured_repo(tmp_path)
    internal, native = "e" * 64, "f" * 40
    repo._write_native_map({internal: native}, "origin")
    _known_client(monkeypatch, {"refs/heads/main": native})

    result = fetch_porcelain(
        repo,
        "origin",
        refspecs=["main"],
        refmap=[""],
        tags=False,
    )

    assert result["refs"] == {"refs/heads/main": internal}
    assert repo.refs.get_remote("origin", "main") is None
    text = (repo.pygit_dir / "FETCH_HEAD").read_text()
    assert text.startswith(internal + "\t\t")


def test_explicit_destination_wins_over_refmap(tmp_path, monkeypatch):
    repo = _configured_repo(tmp_path)
    internal = repo.store.write(CommitObject(message="phase185 explicit destination"))
    native = "2" * 40
    repo._write_native_map({internal: native}, "origin")
    _known_client(monkeypatch, {"refs/heads/main": native})

    fetch_porcelain(
        repo,
        "origin",
        refspecs=["main:local-main"],
        refmap=["+refs/heads/*:refs/remotes/origin/mapped-*"],
        tags=False,
    )

    assert repo.refs.get_branch("local-main") == internal
    assert repo.refs.get_remote("origin", "mapped-main") is None


def test_refmap_requires_command_line_refspecs(tmp_path, monkeypatch):
    repo = _configured_repo(tmp_path)
    monkeypatch.chdir(repo.worktree)

    with pytest.raises(RuntimeError, match="only meaningful with command-line refspec"):
        run_fetch(["--refmap=+refs/heads/*:refs/remotes/origin/x-*", "origin"])


def test_cli_forwards_refmap_values(tmp_path, monkeypatch):
    repo = _configured_repo(tmp_path)
    monkeypatch.chdir(repo.worktree)
    calls = []

    def fake_fetch(repo_arg, remote, **kwargs):
        calls.append((repo_arg.worktree, remote, kwargs))
        return {
            "remote": remote,
            "default_branch": None,
            "refs": {},
            "objects": 0,
            "pruned": [],
            "tag_mode": "none",
        }

    monkeypatch.setattr("pygit.fetch_cli.fetch_porcelain", fake_fetch)
    assert run_fetch([
        "--refmap=+refs/heads/*:refs/remotes/origin/one-*",
        "--refmap=+refs/heads/*:refs/remotes/origin/two-*",
        "origin",
        "main",
    ]) == 0

    assert calls[0][1] == "origin"
    assert calls[0][2]["refspecs"] == ["main"]
    assert calls[0][2]["refmap"] == [
        "+refs/heads/*:refs/remotes/origin/one-*",
        "+refs/heads/*:refs/remotes/origin/two-*",
    ]
