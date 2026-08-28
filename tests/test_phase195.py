from __future__ import annotations

from pathlib import Path

from pygit.fetch_cli import run_fetch
from pygit.fetch_policy import parse_fetch_refspec
from pygit.fetch_prefetch import (
    delete_prefetch_ref,
    list_prefetch_refs,
    prefetch_refspec,
    set_prefetch_ref,
)
from pygit.fetch_prefetch_run import fetch_prefetched
from pygit.remote import Advertisement
from pygit.repo import Repository


def _repo(tmp_path: Path) -> Repository:
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")
    repo.config_set("remote", "origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
    return repo


def test_prefetch_refspec_rewrites_only_destination_namespace():
    spec = prefetch_refspec(parse_fetch_refspec("+refs/heads/*:refs/remotes/origin/*"))
    assert spec.source == "refs/heads/*"
    assert spec.destination == "refs/prefetch/remotes/origin/*"
    assert spec.force is True

    negative = prefetch_refspec(parse_fetch_refspec("^refs/heads/private/*"))
    assert negative.source == "refs/heads/private/*"
    assert negative.destination is None
    assert negative.negative is True


def test_prefetch_ref_helpers_round_trip_sha256(tmp_path):
    repo = _repo(tmp_path)
    refname = "refs/prefetch/remotes/origin/main"
    sha = "a" * 64

    set_prefetch_ref(repo, refname, sha)
    assert list_prefetch_refs(repo) == [refname]
    assert (repo.pygit_dir / "refs/prefetch/remotes/origin/main").read_text() == sha

    delete_prefetch_ref(repo, refname)
    assert list_prefetch_refs(repo) == []


def test_prefetched_fetch_updates_prefetch_not_remote_tracking(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    imported = {"refs/heads/main": "a" * 64, "refs/heads/dev": "b" * 64}

    class Client:
        def __init__(self, url):
            assert url == "https://example.test/repo.git"

        def discover(self):
            return Advertisement(
                {"refs/heads/main": "1" * 40, "refs/heads/dev": "2" * 40},
                set(),
                {"HEAD": "refs/heads/main"},
            )

    monkeypatch.setattr("pygit.fetch_prefetch_run.SmartHttpClient", Client)
    monkeypatch.setattr(
        "pygit.fetch_prefetch_run.fetch_porcelain",
        lambda *a, **k: {
            "remote": "origin",
            "default_branch": None,
            "refs": imported,
            "objects": 0,
            "pruned": [],
            "tag_mode": "auto",
        },
    )

    result = fetch_prefetched(repo, "origin", write_fetch_head_enabled=False)

    assert result["default_branch"] == "main"
    assert repo.refs.get_remote("origin", "main") is None
    assert (repo.pygit_dir / "refs/prefetch/remotes/origin/main").read_text() == "a" * 64
    assert (repo.pygit_dir / "refs/prefetch/remotes/origin/dev").read_text() == "b" * 64


def test_prefetch_prune_uses_rewritten_configured_domain(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    set_prefetch_ref(repo, "refs/prefetch/remotes/origin/main", "a" * 64)
    set_prefetch_ref(repo, "refs/prefetch/remotes/origin/gone", "b" * 64)

    class Client:
        def __init__(self, url):
            pass

        def discover(self):
            return Advertisement(
                {"refs/heads/main": "1" * 40}, set(), {"HEAD": "refs/heads/main"}
            )

    monkeypatch.setattr("pygit.fetch_prefetch_run.SmartHttpClient", Client)
    monkeypatch.setattr(
        "pygit.fetch_prefetch_run.fetch_porcelain",
        lambda *a, **k: {
            "remote": "origin",
            "default_branch": None,
            "refs": {"refs/heads/main": "a" * 64},
            "objects": 0,
            "pruned": [],
            "tag_mode": "auto",
        },
    )

    result = fetch_prefetched(
        repo, "origin", prune=True, write_fetch_head_enabled=False
    )
    assert result["pruned"] == ["refs/prefetch/remotes/origin/gone"]
    assert list_prefetch_refs(repo) == ["refs/prefetch/remotes/origin/main"]


def test_explicit_destination_stays_explicit_while_source_only_uses_prefetch(tmp_path, monkeypatch):
    repo = _repo(tmp_path)

    class Client:
        def __init__(self, url):
            pass

        def discover(self):
            return Advertisement(
                {"refs/heads/main": "1" * 40, "refs/heads/dev": "2" * 40},
                set(),
                {"HEAD": "refs/heads/main"},
            )

    monkeypatch.setattr("pygit.fetch_prefetch_run.SmartHttpClient", Client)
    monkeypatch.setattr(
        "pygit.fetch_prefetch_run.fetch_porcelain",
        lambda *a, **k: {
            "remote": "origin",
            "default_branch": None,
            "refs": {"refs/heads/main": "a" * 64, "refs/heads/dev": "b" * 64},
            "objects": 0,
            "pruned": [],
            "tag_mode": "auto",
        },
    )

    fetch_prefetched(
        repo,
        "origin",
        refspecs=["main", "dev:peek"],
        write_fetch_head_enabled=False,
    )

    assert (repo.pygit_dir / "refs/prefetch/remotes/origin/main").read_text() == "a" * 64
    assert not (repo.pygit_dir / "refs/prefetch/remotes/origin/dev").exists()


def test_cli_forwards_prefetch_for_single_and_multiple_named_remotes(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path)
    repo.add_remote("backup", "https://example.test/backup.git")
    monkeypatch.chdir(tmp_path / "repo")
    capsys.readouterr()
    calls = []

    def fake(repo_arg, remote, **kwargs):
        calls.append((remote, kwargs))
        return {
            "remote": remote,
            "default_branch": "main",
            "refs": {},
            "objects": 0,
            "pruned": [],
        }

    monkeypatch.setattr("pygit.fetch_cli.fetch_prefetched", fake)

    assert run_fetch(["--prefetch", "--no-write-fetch-head", "origin"]) == 0
    assert calls[-1][0] == "origin"

    calls.clear()
    assert run_fetch([
        "--prefetch",
        "--quiet",
        "--no-write-fetch-head",
        "--multiple",
        "origin",
        "backup",
    ]) == 0
    assert [remote for remote, _ in calls] == ["origin", "backup"]
