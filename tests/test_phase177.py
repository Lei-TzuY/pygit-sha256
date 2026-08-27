from __future__ import annotations

import pytest

from pygit.push_cli import run_push
from pygit.push_urls import remote_push_urls, use_push_url
from pygit.repo import Repository


def _commit(repo: Repository) -> str:
    path = repo.worktree / "a.txt"
    path.write_text("A\n", encoding="utf-8")
    repo.add(["a.txt"])
    return repo.commit("c1", author_name="Test", author_email="test@example.com")


def _repo(tmp_path) -> Repository:
    repo = Repository.init(str(tmp_path / "repo"))
    _commit(repo)
    repo.add_remote("origin", "https://fetch.example/origin.git")
    return repo


def _write_remote_values(repo: Repository, lines: list[str]) -> None:
    body = "[remote]\n" + "\n".join(lines) + "\n"
    (repo.pygit_dir / "config").write_text(body, encoding="utf-8")


def test_push_urls_fall_back_to_legacy_json_remote(tmp_path):
    repo = _repo(tmp_path)
    assert remote_push_urls(repo, "origin") == ("https://fetch.example/origin.git",)


def test_pushurl_replaces_all_configured_urls(tmp_path):
    repo = _repo(tmp_path)
    _write_remote_values(
        repo,
        [
            "origin.url = https://fetch-1.example/repo.git",
            "origin.url = https://fetch-2.example/repo.git",
            "origin.pushurl = https://push-1.example/repo.git",
            "origin.pushurl = https://push-2.example/repo.git",
        ],
    )
    assert remote_push_urls(repo, "origin") == (
        "https://push-1.example/repo.git",
        "https://push-2.example/repo.git",
    )


def test_all_urls_are_push_destinations_when_pushurl_is_absent(tmp_path):
    repo = _repo(tmp_path)
    _write_remote_values(
        repo,
        [
            "origin.url = https://one.example/repo.git",
            "origin.url = https://two.example/repo.git",
        ],
    )
    assert remote_push_urls(repo, "origin") == (
        "https://one.example/repo.git",
        "https://two.example/repo.git",
    )


def test_empty_pushurl_clears_earlier_values_and_falls_back_to_urls(tmp_path):
    repo = _repo(tmp_path)
    _write_remote_values(
        repo,
        [
            "origin.pushurl = https://discard.example/repo.git",
            "origin.pushurl =",
            "origin.url = https://one.example/repo.git",
            "origin.url = https://two.example/repo.git",
        ],
    )
    assert remote_push_urls(repo, "origin") == (
        "https://one.example/repo.git",
        "https://two.example/repo.git",
    )


def test_scoped_push_url_changes_only_in_memory_view(tmp_path):
    repo = _repo(tmp_path)
    config_path = repo.pygit_dir / "config.json"
    before = config_path.read_text(encoding="utf-8")
    assert "_read_config" not in repo.__dict__

    with use_push_url(repo, "origin", "https://push.example/repo.git"):
        assert repo._read_config()["remotes"]["origin"]["url"] == "https://push.example/repo.git"
        assert config_path.read_text(encoding="utf-8") == before

    assert "_read_config" not in repo.__dict__
    assert repo._read_config()["remotes"]["origin"]["url"] == "https://fetch.example/origin.git"
    assert config_path.read_text(encoding="utf-8") == before


def test_cli_runs_complete_push_pass_for_each_pushurl(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_remote_values(
        repo,
        [
            "origin.pushurl = https://push-1.example/repo.git",
            "origin.pushurl = https://push-2.example/repo.git",
        ],
    )
    seen = []

    def fake_push_url(repo_obj, args, parser, remote, lease, push_options, follow_tags_enabled):
        seen.append((remote, repo_obj._read_config()["remotes"][remote]["url"], tuple(args.refspecs)))
        return 0

    monkeypatch.setattr("pygit.push_cli.find_repo", lambda: repo)
    monkeypatch.setattr("pygit.push_cli._run_one_push_url", fake_push_url)

    assert run_push(["origin", "main"]) == 0
    assert seen == [
        ("origin", "https://push-1.example/repo.git", ("main",)),
        ("origin", "https://push-2.example/repo.git", ("main",)),
    ]


def test_cli_stops_after_first_pushurl_failure(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_remote_values(
        repo,
        [
            "origin.pushurl = https://bad.example/repo.git",
            "origin.pushurl = https://never.example/repo.git",
        ],
    )
    seen = []

    def fake_push_url(repo_obj, args, parser, remote, lease, push_options, follow_tags_enabled):
        url = repo_obj._read_config()["remotes"][remote]["url"]
        seen.append(url)
        raise RuntimeError("first destination failed")

    monkeypatch.setattr("pygit.push_cli.find_repo", lambda: repo)
    monkeypatch.setattr("pygit.push_cli._run_one_push_url", fake_push_url)

    with pytest.raises(RuntimeError, match="first destination failed"):
        run_push(["origin", "main"])
    assert seen == ["https://bad.example/repo.git"]


def test_atomic_flag_is_replayed_for_every_pushurl(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_remote_values(
        repo,
        [
            "origin.pushurl = https://push-1.example/repo.git",
            "origin.pushurl = https://push-2.example/repo.git",
        ],
    )
    seen = []

    def fake_push_url(repo_obj, args, parser, remote, lease, push_options, follow_tags_enabled):
        seen.append((repo_obj._read_config()["remotes"][remote]["url"], args.atomic))
        return 0

    monkeypatch.setattr("pygit.push_cli.find_repo", lambda: repo)
    monkeypatch.setattr("pygit.push_cli._run_one_push_url", fake_push_url)

    assert run_push(["--atomic", "origin", "main"]) == 0
    assert seen == [
        ("https://push-1.example/repo.git", True),
        ("https://push-2.example/repo.git", True),
    ]


def test_remote_group_continues_after_one_members_pushurl_failure(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    repo.add_remote("backup", "https://backup.example/repo.git")
    _write_remote_values(
        repo,
        [
            "origin.pushurl = https://bad.example/repo.git",
            "origin.pushurl = https://never.example/repo.git",
            "backup.pushurl = https://backup-push.example/repo.git",
            "remotes-placeholder = ignored",
        ],
    )
    # Groups use the dedicated [remotes] section in pygit's flattened config.
    with (repo.pygit_dir / "config").open("a", encoding="utf-8") as handle:
        handle.write("\n[remotes]\npair = origin backup\n")

    seen = []

    def fake_push_url(repo_obj, args, parser, remote, lease, push_options, follow_tags_enabled):
        url = repo_obj._read_config()["remotes"][remote]["url"]
        seen.append((remote, url))
        if remote == "origin":
            raise RuntimeError("origin failed")
        return 0

    monkeypatch.setattr("pygit.push_cli.find_repo", lambda: repo)
    monkeypatch.setattr("pygit.push_cli._run_one_push_url", fake_push_url)

    assert run_push(["pair", "main"]) == 1
    assert seen == [
        ("origin", "https://bad.example/repo.git"),
        ("backup", "https://backup-push.example/repo.git"),
    ]
