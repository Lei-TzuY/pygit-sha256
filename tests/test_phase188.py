from __future__ import annotations

import pytest

import pygit.fetch_cli as fetch_cli
from pygit.fetch_multiple import (
    all_fetch_remotes,
    expand_fetch_sources,
    fetch_all_by_config,
    remote_group_members,
    run_multi_fetch,
)
from pygit.repo import Repository


def _repo(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.invalid/origin.git")
    repo.add_remote("backup", "https://example.invalid/backup.git")
    return repo


def test_fetch_all_filters_skip_fetch_all(tmp_path):
    repo = _repo(tmp_path)
    repo.config_set("remote", "backup.skipFetchAll", "true")
    assert all_fetch_remotes(repo) == ["origin"]


def test_fetch_all_accepts_git_boolean_spelling(tmp_path):
    repo = _repo(tmp_path)
    repo.config_set("remote", "origin.skipFetchAll", "yes")
    repo.config_set("remote", "backup.skipFetchAll", "off")
    assert all_fetch_remotes(repo) == ["backup"]


def test_fetch_all_rejects_invalid_boolean(tmp_path):
    repo = _repo(tmp_path)
    repo.config_set("remote", "backup.skipFetchAll", "sometimes")
    with pytest.raises(ValueError, match="invalid boolean"):
        all_fetch_remotes(repo)


def test_group_expansion_preserves_order_and_duplicates(tmp_path):
    repo = _repo(tmp_path)
    repo.config_set("remotes", "mirror-set", "origin backup origin")
    assert remote_group_members(repo, "mirror-set") == (
        "origin",
        "backup",
        "origin",
    )
    assert expand_fetch_sources(repo, ["mirror-set", "backup"]) == [
        "origin",
        "backup",
        "origin",
        "backup",
    ]


def test_empty_group_is_rejected(tmp_path):
    repo = _repo(tmp_path)
    repo.config_set("remotes", "empty", "")
    with pytest.raises(RuntimeError, match="has no members"):
        remote_group_members(repo, "empty")


def test_fetch_all_config_defaults_false_and_can_enable(tmp_path):
    repo = _repo(tmp_path)
    assert fetch_all_by_config(repo) is False
    repo.config_set("fetch", "all", "true")
    assert fetch_all_by_config(repo) is True


def test_multi_fetch_continues_after_failure_and_appends(tmp_path):
    repo = _repo(tmp_path)
    calls = []

    def one(remote, append):
        calls.append((remote, append))
        if remote == "origin":
            raise RuntimeError("boom")

    results = run_multi_fetch(repo, ["origin", "backup"], one)
    assert calls == [("origin", False), ("backup", True)]
    assert [result.ok for result in results] == [False, True]
    assert results[0].error == "boom"


def test_cli_all_honors_skip_and_aggregates_fetch_head(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    repo.config_set("remote", "backup.skipFetchAll", "true")
    calls = []

    monkeypatch.chdir(repo.worktree)
    monkeypatch.setattr(
        fetch_cli,
        "_fetch_named",
        lambda repo, remote, **kwargs: calls.append((remote, kwargs["append"])),
    )

    assert fetch_cli.run_fetch(["--all"]) == 0
    assert calls == [("origin", False)]


def test_cli_multiple_expands_groups_and_preserves_duplicates(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    repo.config_set("remotes", "both", "origin backup")
    calls = []

    monkeypatch.chdir(repo.worktree)
    monkeypatch.setattr(
        fetch_cli,
        "_fetch_named",
        lambda repo, remote, **kwargs: calls.append((remote, kwargs["append"])),
    )

    assert fetch_cli.run_fetch(["--multiple", "both", "origin"]) == 0
    assert calls == [
        ("origin", False),
        ("backup", True),
        ("origin", True),
    ]


def test_cli_single_group_is_multi_fetch(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    repo.config_set("remotes", "both", "origin backup")
    calls = []

    monkeypatch.chdir(repo.worktree)
    monkeypatch.setattr(
        fetch_cli,
        "_fetch_named",
        lambda repo, remote, **kwargs: calls.append((remote, kwargs["append"])),
    )

    assert fetch_cli.run_fetch(["both"]) == 0
    assert calls == [("origin", False), ("backup", True)]


def test_cli_multi_failure_continues_and_returns_nonzero(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path)
    calls = []

    def fake(repo, remote, **kwargs):
        calls.append(remote)
        if remote == "origin":
            raise RuntimeError("cannot connect")

    monkeypatch.chdir(repo.worktree)
    monkeypatch.setattr(fetch_cli, "_fetch_named", fake)

    assert fetch_cli.run_fetch(["--multiple", "origin", "backup"]) == 1
    assert calls == ["origin", "backup"]
    assert "could not fetch origin" in capsys.readouterr().err


def test_fetch_all_config_applies_only_without_explicit_repository(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    repo.config_set("fetch", "all", "true")
    multi_calls = []

    monkeypatch.chdir(repo.worktree)
    monkeypatch.setattr(
        fetch_cli,
        "_fetch_named",
        lambda repo, remote, **kwargs: multi_calls.append(remote),
    )
    assert fetch_cli.run_fetch([]) == 0
    assert multi_calls == ["origin", "backup"]

    result = {
        "remote": "backup",
        "default_branch": None,
        "refs": {},
        "objects": 0,
        "pruned": [],
        "tag_mode": "auto",
    }
    single_calls = []
    monkeypatch.setattr(
        fetch_cli,
        "fetch_configured",
        lambda repo, remote, **kwargs: single_calls.append(remote) or result,
    )
    monkeypatch.setattr(fetch_cli, "_write_configured_fetch_head", lambda *args: None)
    assert fetch_cli.run_fetch(["backup"]) == 0
    assert single_calls == ["backup"]


def test_no_all_suppresses_fetch_all_config(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    repo.config_set("fetch", "all", "true")
    result = {
        "remote": "origin",
        "default_branch": None,
        "refs": {},
        "objects": 0,
        "pruned": [],
        "tag_mode": "auto",
    }
    calls = []

    monkeypatch.chdir(repo.worktree)
    monkeypatch.setattr(
        fetch_cli,
        "fetch_configured",
        lambda repo, remote, **kwargs: calls.append(remote) or result,
    )
    monkeypatch.setattr(fetch_cli, "_write_configured_fetch_head", lambda *args: None)

    assert fetch_cli.run_fetch(["--no-all"]) == 0
    assert calls == ["origin"]


def test_multiple_rejects_refmap_and_requires_sources(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo.worktree)
    with pytest.raises(RuntimeError, match="requires at least one"):
        fetch_cli.run_fetch(["--multiple"])
    with pytest.raises(RuntimeError, match="incompatible with --multiple"):
        fetch_cli.run_fetch(["--multiple", "--refmap=x:y", "origin"])


def test_all_rejects_explicit_repository(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo.worktree)
    with pytest.raises(RuntimeError, match="does not accept"):
        fetch_cli.run_fetch(["--all", "origin"])
