from __future__ import annotations

import pytest

from pygit.push_urls import remote_push_urls
from pygit.remote_cli import run_remote
from pygit.remote_query import resolve_remote_url
from pygit.remote_urls import fetch_url, get_remote_urls, set_remote_url
from pygit.repo import Repository


def _repo(tmp_path) -> Repository:
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://legacy.example/repo.git")
    return repo


def _write_remote_values(repo: Repository, lines: list[str]) -> None:
    (repo.pygit_dir / "config").write_text(
        "[remote]\n" + "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def test_get_url_defaults_to_first_fetch_url_and_all_lists_every_url(tmp_path):
    repo = _repo(tmp_path)
    _write_remote_values(
        repo,
        [
            "origin.url = https://one.example/repo.git",
            "origin.url = https://two.example/repo.git",
        ],
    )
    assert get_remote_urls(repo, "origin") == ("https://one.example/repo.git",)
    assert get_remote_urls(repo, "origin", all_urls=True) == (
        "https://one.example/repo.git",
        "https://two.example/repo.git",
    )
    assert fetch_url(repo, "origin") == "https://one.example/repo.git"
    assert resolve_remote_url("origin", repo) == "https://one.example/repo.git"


def test_get_url_push_falls_back_to_fetch_urls_until_pushurl_exists(tmp_path):
    repo = _repo(tmp_path)
    _write_remote_values(
        repo,
        [
            "origin.url = https://one.example/repo.git",
            "origin.url = https://two.example/repo.git",
        ],
    )
    assert get_remote_urls(repo, "origin", push=True, all_urls=True) == (
        "https://one.example/repo.git",
        "https://two.example/repo.git",
    )
    set_remote_url(repo, "origin", "https://push.example/repo.git", push=True)
    assert get_remote_urls(repo, "origin", push=True, all_urls=True) == (
        "https://push.example/repo.git",
    )


def test_set_url_replaces_first_and_syncs_legacy_fetch_endpoint(tmp_path):
    repo = _repo(tmp_path)
    _write_remote_values(
        repo,
        [
            "origin.url = https://one.example/repo.git",
            "origin.url = https://two.example/repo.git",
        ],
    )
    assert set_remote_url(repo, "origin", "https://new.example/repo.git") == (
        "https://new.example/repo.git",
        "https://two.example/repo.git",
    )
    assert repo._read_config()["remotes"]["origin"]["url"] == "https://new.example/repo.git"


def test_set_url_regex_replaces_first_match_only(tmp_path):
    repo = _repo(tmp_path)
    _write_remote_values(
        repo,
        [
            "origin.url = https://one.example/repo.git",
            "origin.url = https://two.example/repo.git",
            "origin.url = https://two.example/other.git",
        ],
    )
    values = set_remote_url(
        repo,
        "origin",
        "https://replacement.example/repo.git",
        old_url=r"two\.example",
    )
    assert values == (
        "https://one.example/repo.git",
        "https://replacement.example/repo.git",
        "https://two.example/other.git",
    )


def test_set_url_regex_miss_does_not_mutate_config(tmp_path):
    repo = _repo(tmp_path)
    _write_remote_values(repo, ["origin.url = https://one.example/repo.git"])
    before = (repo.pygit_dir / "config").read_text(encoding="utf-8")
    with pytest.raises(RuntimeError, match="No such URL found"):
        set_remote_url(repo, "origin", "https://new.example/repo.git", old_url="missing")
    assert (repo.pygit_dir / "config").read_text(encoding="utf-8") == before


def test_set_url_add_materializes_legacy_then_appends(tmp_path):
    repo = _repo(tmp_path)
    values = set_remote_url(repo, "origin", "https://two.example/repo.git", add=True)
    assert values == (
        "https://legacy.example/repo.git",
        "https://two.example/repo.git",
    )
    assert get_remote_urls(repo, "origin", all_urls=True) == values


def test_set_url_delete_removes_all_regex_matches_and_updates_primary(tmp_path):
    repo = _repo(tmp_path)
    _write_remote_values(
        repo,
        [
            "origin.url = https://old.example/one.git",
            "origin.url = https://old.example/two.git",
            "origin.url = https://keep.example/repo.git",
        ],
    )
    values = set_remote_url(repo, "origin", r"old\.example", delete=True)
    assert values == ("https://keep.example/repo.git",)
    assert repo._read_config()["remotes"]["origin"]["url"] == "https://keep.example/repo.git"


def test_set_url_refuses_to_delete_all_fetch_urls(tmp_path):
    repo = _repo(tmp_path)
    with pytest.raises(RuntimeError, match="Will not delete all non-push URLs"):
        set_remote_url(repo, "origin", r"legacy\.example", delete=True)
    assert get_remote_urls(repo, "origin") == ("https://legacy.example/repo.git",)


def test_deleting_all_pushurls_is_allowed_and_restores_fetch_fallback(tmp_path):
    repo = _repo(tmp_path)
    set_remote_url(repo, "origin", "https://push.example/repo.git", push=True)
    assert set_remote_url(repo, "origin", r"push\.example", push=True, delete=True) == ()
    assert remote_push_urls(repo, "origin") == ("https://legacy.example/repo.git",)


def test_cli_get_and_set_url(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path)
    monkeypatch.setattr("pygit.remote_cli.find_repo", lambda: repo)

    assert run_remote(["get-url", "origin"]) == 0
    assert capsys.readouterr().out.strip() == "https://legacy.example/repo.git"

    assert run_remote(["set-url", "--add", "origin", "https://two.example/repo.git"]) == 0
    assert run_remote(["get-url", "--all", "origin"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "https://legacy.example/repo.git",
        "https://two.example/repo.git",
    ]

    assert run_remote(["set-url", "--push", "origin", "https://push.example/repo.git"]) == 0
    assert run_remote(["get-url", "--push", "origin"]) == 0
    assert capsys.readouterr().out.strip() == "https://push.example/repo.git"
