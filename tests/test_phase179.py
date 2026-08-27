"""Phase179 remote lifecycle configuration synchronization regressions."""

from __future__ import annotations

import pytest

from pygit import Repository
from pygit.config import GitConfig
from pygit.remote_cli import run_remote
from pygit.remote_lifecycle import add_remote, remove_remote, rename_remote
from pygit.remote_urls import fetch_urls, push_urls, set_remote_url


def _config(repo: Repository) -> GitConfig:
    return GitConfig(repo.pygit_dir)


def test_add_materializes_url_and_default_fetch_refspec(tmp_path):
    repo = Repository.init(str(tmp_path))

    add_remote(repo, "origin", "https://example.test/repo.git")

    assert repo.list_remotes() == {"origin": "https://example.test/repo.git"}
    cfg = _config(repo)
    assert cfg.get_all("remote", "origin.url") == ["https://example.test/repo.git"]
    assert cfg.get_all("remote", "origin.fetch") == [
        "+refs/heads/*:refs/remotes/origin/*"
    ]
    assert fetch_urls(repo, "origin") == ("https://example.test/repo.git",)
    assert push_urls(repo, "origin") == ("https://example.test/repo.git",)


def test_add_rejects_existing_legacy_or_ini_remote(tmp_path):
    repo = Repository.init(str(tmp_path))
    add_remote(repo, "origin", "https://example.test/one.git")

    with pytest.raises(RuntimeError, match="already exists"):
        add_remote(repo, "origin", "https://example.test/two.git")

    repo.remove_remote("origin")
    # Leave the Git-style config behind deliberately: it is still a configured
    # remote name and must not be silently overwritten by modern remote add.
    with pytest.raises(RuntimeError, match="already exists"):
        add_remote(repo, "origin", "https://example.test/three.git")


def test_rename_moves_multivalue_remote_config_and_references(tmp_path):
    repo = Repository.init(str(tmp_path))
    add_remote(repo, "origin", "https://example.test/fetch-a.git")
    set_remote_url(repo, "origin", "https://example.test/fetch-b.git", add=True)
    set_remote_url(repo, "origin", "https://example.test/push-a.git", push=True, add=True)
    set_remote_url(repo, "origin", "https://example.test/push-b.git", push=True, add=True)
    repo.config_set("remote", "origin.mirror", "true")
    repo.config_set("remote", "pushDefault", "origin")
    repo.config_set("branch", "main.remote", "origin")
    repo.config_set("branch", "main.merge", "refs/heads/main")
    repo.config_set("branch", "main.pushRemote", "origin")
    repo.config_set("remotes", "fanout", "origin backup")
    repo.refs.set_remote("origin", "main", "a" * 64)
    repo._write_native_map({"b" * 64: "c" * 40}, "origin")

    rename_remote(repo, "origin", "upstream")

    assert repo.list_remotes() == {"upstream": "https://example.test/fetch-a.git"}
    assert fetch_urls(repo, "upstream") == (
        "https://example.test/fetch-a.git",
        "https://example.test/fetch-b.git",
    )
    assert push_urls(repo, "upstream") == (
        "https://example.test/push-a.git",
        "https://example.test/push-b.git",
    )
    cfg = _config(repo)
    assert cfg.get_all("remote", "origin.url") == []
    assert cfg.get_all("remote", "origin.pushurl") == []
    assert cfg.get_all("remote", "upstream.fetch") == [
        "+refs/heads/*:refs/remotes/upstream/*"
    ]
    assert cfg.get("remote", "upstream.mirror") == "true"
    assert cfg.get("remote", "pushDefault") == "upstream"
    assert cfg.get("branch", "main.remote") == "upstream"
    assert cfg.get("branch", "main.merge") == "refs/heads/main"
    assert cfg.get("branch", "main.pushRemote") == "upstream"
    # Native Git does not rewrite textual remote-group membership on rename.
    assert cfg.get("remotes", "fanout") == "origin backup"
    assert repo.refs.get_remote("upstream", "main") == "a" * 64
    assert repo.refs.list_remotes("origin") == []
    assert repo._read_native_map("upstream") == {"b" * 64: "c" * 40}


def test_remove_clears_remote_config_and_upstream_pair(tmp_path):
    repo = Repository.init(str(tmp_path))
    add_remote(repo, "origin", "https://example.test/repo.git")
    set_remote_url(repo, "origin", "https://example.test/push.git", push=True, add=True)
    repo.config_set("remote", "origin.mirror", "true")
    repo.config_set("remote", "pushDefault", "origin")
    repo.config_set("branch", "main.remote", "origin")
    repo.config_set("branch", "main.merge", "refs/heads/main")
    repo.config_set("branch", "main.pushRemote", "origin")
    repo.config_set("branch", "main.rebase", "true")
    repo.config_set("branch", "main.description", "keep me")
    repo.config_set("remotes", "fanout", "origin backup")
    repo.refs.set_remote("origin", "main", "a" * 64)
    repo._write_native_map({"b" * 64: "c" * 40}, "origin")

    remove_remote(repo, "origin")

    assert repo.list_remotes() == {}
    cfg = _config(repo)
    assert cfg.get_all("remote", "origin.url") == []
    assert cfg.get_all("remote", "origin.pushurl") == []
    assert cfg.get("remote", "origin.mirror") is None
    assert cfg.get("remote", "pushDefault") is None
    assert cfg.get("branch", "main.remote") is None
    assert cfg.get("branch", "main.merge") is None
    assert cfg.get("branch", "main.pushRemote") is None
    assert cfg.get("branch", "main.rebase") == "true"
    assert cfg.get("branch", "main.description") == "keep me"
    assert cfg.get("remotes", "fanout") == "origin backup"
    assert repo.refs.list_remotes("origin") == []
    assert repo._read_native_map("origin") == {}


def test_remove_only_drops_pushremote_when_upstream_is_another_remote(tmp_path):
    repo = Repository.init(str(tmp_path))
    add_remote(repo, "origin", "https://example.test/origin.git")
    add_remote(repo, "backup", "https://example.test/backup.git")
    repo.config_set("branch", "main.remote", "backup")
    repo.config_set("branch", "main.merge", "refs/heads/main")
    repo.config_set("branch", "main.pushRemote", "origin")

    remove_remote(repo, "origin")

    cfg = _config(repo)
    assert cfg.get("branch", "main.remote") == "backup"
    assert cfg.get("branch", "main.merge") == "refs/heads/main"
    assert cfg.get("branch", "main.pushRemote") is None


def test_rename_rejects_destination_present_only_in_ini_config(tmp_path):
    repo = Repository.init(str(tmp_path))
    add_remote(repo, "origin", "https://example.test/origin.git")
    path = repo.pygit_dir / "config"
    with path.open("a", encoding="utf-8") as handle:
        handle.write("backup.url = https://example.test/stale.git\n")

    with pytest.raises(RuntimeError, match="already exists"):
        rename_remote(repo, "origin", "backup")

    assert "origin" in repo.list_remotes()


def test_remote_cli_add_rename_and_rm_alias(tmp_path, monkeypatch):
    Repository.init(str(tmp_path))
    monkeypatch.chdir(tmp_path)

    assert run_remote(["add", "origin", "https://example.test/repo.git"]) == 0
    repo = Repository(str(tmp_path))
    assert fetch_urls(repo, "origin") == ("https://example.test/repo.git",)

    assert run_remote(["rename", "origin", "upstream"]) == 0
    repo = Repository(str(tmp_path))
    assert fetch_urls(repo, "upstream") == ("https://example.test/repo.git",)

    assert run_remote(["rm", "upstream"]) == 0
    assert Repository(str(tmp_path)).list_remotes() == {}


def test_rename_same_name_is_a_valid_noop_for_existing_remote(tmp_path):
    repo = Repository.init(str(tmp_path))
    add_remote(repo, "origin", "https://example.test/repo.git")

    rename_remote(repo, "origin", "origin")

    assert fetch_urls(repo, "origin") == ("https://example.test/repo.git",)


def test_remove_unknown_remote_does_not_touch_ini_config(tmp_path):
    repo = Repository.init(str(tmp_path))
    repo.config_set("branch", "main.description", "keep")
    before = (repo.pygit_dir / "config").read_text(encoding="utf-8")

    with pytest.raises(KeyError, match="Unknown remote"):
        remove_remote(repo, "missing")

    assert (repo.pygit_dir / "config").read_text(encoding="utf-8") == before
