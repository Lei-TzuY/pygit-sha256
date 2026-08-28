from __future__ import annotations

import json

import pytest

from pygit.fetch_cli import run_fetch
from pygit.fetch_direct import fetch_direct_url, is_direct_fetch_url
from pygit.remote import Advertisement
from pygit.repo import Repository


URL = "https://example.test/repo.git"


def _repo(tmp_path):
    return Repository.init(str(tmp_path / "repo"))


def _mock_transport(monkeypatch, refs, imported):
    class Client:
        def __init__(self, url):
            assert url == URL

        def discover(self):
            return Advertisement(refs, set(), {"HEAD": "refs/heads/main"})

    monkeypatch.setattr("pygit.fetch_direct.SmartHttpClient", Client)

    def fake_import(repo, client, advertisement, selected, native_map, known):
        return {name: imported[name] for name in selected}, 3 if selected else 0

    monkeypatch.setattr("pygit.fetch_direct._fetch_import_sources", fake_import)


def test_url_detection_is_limited_to_supported_smart_http():
    assert is_direct_fetch_url("https://example.test/repo.git")
    assert is_direct_fetch_url("http://example.test/repo.git")
    assert not is_direct_fetch_url("origin")
    assert not is_direct_fetch_url("ssh://example.test/repo.git")


def test_direct_url_without_refspec_fetches_head_only(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    native = "1" * 40
    internal = "a" * 64
    _mock_transport(
        monkeypatch,
        {"HEAD": native, "refs/heads/main": native, "refs/heads/dev": "2" * 40},
        {"HEAD": internal},
    )

    result = fetch_direct_url(repo, URL, tags=False)

    assert result["refs"] == {"HEAD": internal}
    assert repo.refs.list_remotes("origin") == []
    assert repo.list_remotes() == {}
    fetch_head = (repo.pygit_dir / "FETCH_HEAD").read_text()
    assert fetch_head.startswith(internal + "\t\t")
    assert "HEAD" in fetch_head


def test_direct_source_only_refspec_updates_fetch_head_not_tracking_refs(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    native = "3" * 40
    internal = "b" * 64
    _mock_transport(monkeypatch, {"refs/heads/dev": native}, {"refs/heads/dev": internal})

    result = fetch_direct_url(repo, URL, refspecs=["dev"], tags=False)

    assert result["refs"] == {"refs/heads/dev": internal}
    assert repo.refs.get_branch("dev") is None
    assert repo.refs.list_remotes("origin") == []
    text = (repo.pygit_dir / "FETCH_HEAD").read_text()
    assert text.startswith(internal + "\t\t")
    assert "branch 'dev'" in text


def test_direct_explicit_destination_updates_requested_local_branch(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    native = "4" * 40
    internal = "c" * 64
    _mock_transport(monkeypatch, {"refs/heads/dev": native}, {"refs/heads/dev": internal})

    fetch_direct_url(repo, URL, refspecs=["dev:peek"], tags=False)

    assert repo.refs.get_branch("peek") == internal
    assert repo.refs.list_remotes("origin") == []


def test_direct_refmap_can_create_explicit_tracking_destination(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    native = "5" * 40
    internal = "d" * 64
    _mock_transport(monkeypatch, {"refs/heads/dev": native}, {"refs/heads/dev": internal})

    fetch_direct_url(
        repo,
        URL,
        refspecs=["dev"],
        refmap=["+refs/heads/*:refs/remotes/peek/*"],
        tags=False,
    )

    assert repo.refs.get_remote("peek", "dev") == internal
    assert repo.list_remotes() == {}


def test_direct_fetch_does_not_write_remote_configuration(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _mock_transport(monkeypatch, {"HEAD": "6" * 40}, {"HEAD": "e" * 64})
    before_json = json.loads((repo.pygit_dir / "config.json").read_text())
    before_ini = (repo.pygit_dir / "config").read_text() if (repo.pygit_dir / "config").exists() else ""

    fetch_direct_url(repo, URL, tags=False)

    assert json.loads((repo.pygit_dir / "config.json").read_text()) == before_json
    after_ini = (repo.pygit_dir / "config").read_text() if (repo.pygit_dir / "config").exists() else ""
    assert after_ini == before_ini


def test_cli_routes_http_url_to_direct_fetch(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo.worktree)
    calls = []

    def fake_direct(repo_arg, url, **kwargs):
        calls.append((repo_arg.worktree, url, kwargs))
        return {"remote": url, "default_branch": None, "refs": {}, "objects": 0, "pruned": [], "tag_mode": "none"}

    monkeypatch.setattr("pygit.fetch_cli.fetch_direct_url", fake_direct)
    assert run_fetch([URL, "dev"]) == 0
    assert calls[0][1] == URL
    assert calls[0][2]["refspecs"] == ["dev"]


def test_cli_rejects_direct_url_prune_until_it_has_a_named_prune_domain(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo.worktree)
    with pytest.raises(RuntimeError, match="direct URL fetch"):
        run_fetch(["--prune", URL, "dev"])
