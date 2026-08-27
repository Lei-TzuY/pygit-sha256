"""Phase180 remote default-branch symbolic-reference regressions."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from pygit import Repository
from pygit.remote_cli import run_remote
from pygit.remote_head import remote_head_target, set_remote_head
from pygit.remote_lifecycle import add_remote, rename_remote


def _remote(repo: Repository, name: str = "origin") -> None:
    add_remote(repo, name, "https://example.test/repo.git")


def test_explicit_set_head_creates_symbolic_alias_and_remote_shorthand(tmp_path):
    repo = Repository.init(str(tmp_path))
    _remote(repo)
    oid = "a" * 64
    repo.refs.set_remote("origin", "main", oid)

    assert set_remote_head(repo, "origin", "main") == "main"

    assert remote_head_target(repo, "origin") == "refs/remotes/origin/main"
    assert repo.refs.get_remote_head("origin") == "main"
    assert repo.refs.get_remote("origin", "HEAD") == oid
    assert repo.refs.resolve("origin") == oid
    assert repo.refs.list_remotes("origin") == ["main"]
    assert (repo.pygit_dir / "refs" / "remotes" / "origin" / "HEAD").read_text(
        encoding="utf-8"
    ) == "ref: refs/remotes/origin/main\n"


def test_explicit_set_head_requires_existing_tracking_branch_without_mutation(tmp_path):
    repo = Repository.init(str(tmp_path))
    _remote(repo)
    repo.refs.set_remote("origin", "main", "a" * 64)
    set_remote_head(repo, "origin", "main")

    with pytest.raises(RuntimeError, match="Not a valid ref"):
        set_remote_head(repo, "origin", "missing")

    assert repo.refs.get_remote_head("origin") == "main"


def test_delete_remote_head_is_idempotent_and_preserves_tracking_branch(tmp_path):
    repo = Repository.init(str(tmp_path))
    _remote(repo)
    repo.refs.set_remote("origin", "main", "a" * 64)
    set_remote_head(repo, "origin", "main")

    assert set_remote_head(repo, "origin", delete=True) is None
    assert set_remote_head(repo, "origin", delete=True) is None

    assert remote_head_target(repo, "origin") is None
    assert repo.refs.get_remote("origin", "main") == "a" * 64
    assert repo.refs.resolve("origin") is None


def test_auto_uses_advertised_head_and_existing_tracking_branch(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path))
    _remote(repo)
    repo.refs.set_remote("origin", "next", "b" * 64)

    class FakeClient:
        def __init__(self, url):
            assert url == "https://example.test/repo.git"

        def discover(self):
            return SimpleNamespace(
                symrefs={"HEAD": "refs/heads/next"},
                refs={"refs/heads/next": "1" * 40},
            )

    monkeypatch.setattr("pygit.remote_head.SmartHttpClient", FakeClient)

    assert set_remote_head(repo, "origin", auto=True) == "next"
    assert repo.refs.get_remote_head("origin") == "next"


def test_auto_rejects_advertised_branch_that_has_not_been_fetched(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path))
    _remote(repo)
    repo.refs.set_remote("origin", "main", "a" * 64)
    set_remote_head(repo, "origin", "main")

    class FakeClient:
        def __init__(self, url):
            pass

        def discover(self):
            return SimpleNamespace(
                symrefs={"HEAD": "refs/heads/next"},
                refs={"refs/heads/next": "1" * 40},
            )

    monkeypatch.setattr("pygit.remote_head.SmartHttpClient", FakeClient)

    with pytest.raises(RuntimeError, match="Not a valid ref"):
        set_remote_head(repo, "origin", auto=True)

    assert repo.refs.get_remote_head("origin") == "main"


def test_remote_rename_rewrites_symbolic_head_target(tmp_path):
    repo = Repository.init(str(tmp_path))
    _remote(repo)
    repo.refs.set_remote("origin", "main", "a" * 64)
    set_remote_head(repo, "origin", "main")

    rename_remote(repo, "origin", "upstream")

    assert repo.refs.get_remote_head("upstream") == "main"
    assert remote_head_target(repo, "upstream") == "refs/remotes/upstream/main"
    assert repo.refs.resolve("upstream") == "a" * 64
    assert not (repo.pygit_dir / "refs" / "remotes" / "origin").exists()


def test_unknown_remote_is_rejected_before_head_mutation(tmp_path):
    repo = Repository.init(str(tmp_path))
    with pytest.raises(KeyError, match="Unknown remote"):
        set_remote_head(repo, "missing", delete=True)


def test_remote_set_head_cli_matches_native_presentation(tmp_path, monkeypatch, capsys):
    repo = Repository.init(str(tmp_path))
    _remote(repo)
    repo.refs.set_remote("origin", "main", "a" * 64)
    repo.refs.set_remote("origin", "next", "b" * 64)
    monkeypatch.chdir(tmp_path)
    capsys.readouterr()

    assert run_remote(["set-head", "origin", "main"]) == 0
    assert capsys.readouterr().out == ""

    class FakeClient:
        def __init__(self, url):
            pass

        def discover(self):
            return SimpleNamespace(
                symrefs={"HEAD": "refs/heads/next"},
                refs={"refs/heads/next": "1" * 40},
            )

    monkeypatch.setattr("pygit.remote_head.SmartHttpClient", FakeClient)
    assert run_remote(["set-head", "origin", "--auto"]) == 0
    assert capsys.readouterr().out == "origin/HEAD set to next\n"

    assert run_remote(["set-head", "origin", "--delete"]) == 0
    assert capsys.readouterr().out == ""
    assert Repository(str(tmp_path)).refs.get_remote_head("origin") is None
