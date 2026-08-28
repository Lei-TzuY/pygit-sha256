from __future__ import annotations

from pathlib import Path

import pytest

from pygit.config import GitConfig
from pygit.fetch_cli_dry_run import run_fetch
from pygit.fetch_upstream import set_fetch_upstream
from pygit.repo import Repository
from pygit.remote_ops import configured_upstream


def _repo(tmp_path: Path, monkeypatch) -> Repository:
    repo = Repository.init(str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return repo


def test_set_fetch_upstream_records_named_remote(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    assert set_fetch_upstream(repo, "origin", ["main"])
    upstream = configured_upstream(repo, "main")
    assert upstream is not None
    assert upstream.remote == "origin"
    assert upstream.branch == "main"


def test_set_fetch_upstream_accepts_direct_url(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    url = "https://example.test/repo.git"
    assert set_fetch_upstream(repo, url, ["main:peek"])
    upstream = configured_upstream(repo, "main")
    assert upstream is not None
    assert upstream.remote == url
    assert upstream.branch == "main"


def test_set_fetch_upstream_warns_without_source_branch(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path, monkeypatch)
    assert not set_fetch_upstream(repo, "origin", [])
    assert "no source branch found" in capsys.readouterr().err
    assert configured_upstream(repo, "main") is None


def test_set_fetch_upstream_warns_for_multiple_branches(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path, monkeypatch)
    assert not set_fetch_upstream(repo, "origin", ["main", "dev"])
    assert "multiple branches detected" in capsys.readouterr().err
    assert configured_upstream(repo, "main") is None


def test_set_fetch_upstream_ignores_negative_refspecs(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    assert set_fetch_upstream(repo, "origin", ["main", "^private"])
    assert configured_upstream(repo, "main").branch == "main"


def test_fetch_wrapper_sets_upstream_only_after_success(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    calls = []

    def fake_fetch(argv):
        calls.append(list(argv))
        return 0

    monkeypatch.setattr("pygit.fetch_cli_dry_run._run_fetch", fake_fetch)
    assert run_fetch(["--set-upstream", "origin", "dev"]) == 0
    assert calls == [["origin", "dev"]]
    upstream = configured_upstream(repo, "main")
    assert upstream.remote == "origin"
    assert upstream.branch == "dev"


def test_fetch_wrapper_does_not_set_upstream_after_failure(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    monkeypatch.setattr("pygit.fetch_cli_dry_run._run_fetch", lambda argv: 1)
    assert run_fetch(["--set-upstream", "origin", "dev"]) == 1
    assert configured_upstream(repo, "main") is None


def test_fetch_dry_run_restores_upstream_config(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    monkeypatch.setattr("pygit.fetch_cli_dry_run._run_fetch", lambda argv: 0)
    assert run_fetch(["--dry-run", "--set-upstream", "origin", "dev"]) == 0
    assert configured_upstream(repo, "main") is None


def test_set_upstream_after_double_dash_is_literal_refspec(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(
        "pygit.fetch_cli_dry_run._run_fetch", lambda argv: calls.append(list(argv)) or 0
    )
    assert run_fetch(["origin", "--", "--set-upstream"]) == 0
    assert calls == [["origin", "--", "--set-upstream"]]


def test_config_written_in_existing_flat_branch_section(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    config = GitConfig(repo.pygit_dir)
    config.set("branch", "main.rebase", "true")
    assert set_fetch_upstream(repo, "origin", ["refs/heads/main"])
    config = GitConfig(repo.pygit_dir)
    assert config.get("branch", "main.rebase") == "true"
    assert config.get("branch", "main.remote") == "origin"
    assert config.get("branch", "main.merge") == "refs/heads/main"
