from __future__ import annotations

import pytest

from pygit.pull_cli import run_pull
from pygit.push_cli import run_push
from pygit.remote_ops import configured_upstream, resolve_pull_source, resolve_push_remote
from pygit.repo import Repository


def _commit(repo: Repository, name: str, text: str) -> str:
    path = repo.worktree / name
    path.write_text(text, encoding="utf-8")
    repo.add([name])
    return repo.commit(text, author_name="Test", author_email="test@example.com")


def test_configured_upstream_reads_remote_and_merge(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    _commit(repo, "a.txt", "one")
    repo.config_set("branch", "main.remote", "backup")
    repo.config_set("branch", "main.merge", "refs/heads/release")

    upstream = configured_upstream(repo)
    assert upstream is not None
    assert (upstream.remote, upstream.branch, upstream.display) == (
        "backup",
        "release",
        "backup/release",
    )


def test_configured_upstream_rejects_partial_config(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    _commit(repo, "a.txt", "one")
    repo.config_set("branch", "main.remote", "origin")
    with pytest.raises(RuntimeError, match="incomplete upstream"):
        configured_upstream(repo)


def test_pull_defaults_to_origin_current_without_tracking(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    _commit(repo, "a.txt", "one")
    source = resolve_pull_source(repo)
    assert (source.remote, source.branch) == ("origin", "main")


def test_pull_explicit_remote_uses_matching_configured_merge(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    _commit(repo, "a.txt", "one")
    repo.config_set("branch", "main.remote", "backup")
    repo.config_set("branch", "main.merge", "refs/heads/release")
    assert resolve_pull_source(repo, "backup").branch == "release"
    assert resolve_pull_source(repo, "origin").branch == "main"


def test_push_remote_precedence(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    _commit(repo, "a.txt", "one")
    repo.add_remote("origin", "https://example.invalid/origin.git")
    repo.add_remote("publish", "https://example.invalid/publish.git")
    repo.add_remote("fork", "https://example.invalid/fork.git")
    repo.config_set("branch", "main.remote", "origin")
    repo.config_set("branch", "main.merge", "refs/heads/main")

    assert resolve_push_remote(repo) == "origin"
    repo.config_set("remote", "pushDefault", "publish")
    assert resolve_push_remote(repo) == "publish"
    repo.config_set("branch", "main.pushRemote", "fork")
    assert resolve_push_remote(repo) == "fork"
    assert resolve_push_remote(repo, "origin") == "origin"


def test_pull_local_upstream_fast_forwards(tmp_path, monkeypatch, capsys):
    repo = Repository.init(str(tmp_path / "repo"))
    base = _commit(repo, "a.txt", "base")
    repo.branch("topic", start_point="main")
    repo.checkout("topic")
    tip = _commit(repo, "a.txt", "topic")
    repo.checkout("main")
    assert repo.refs.resolve_head() == base
    repo.config_set("branch", "main.remote", ".")
    repo.config_set("branch", "main.merge", "refs/heads/topic")

    monkeypatch.chdir(repo.worktree)
    assert run_pull([]) == 0
    reopened = Repository(str(repo.worktree))
    assert reopened.refs.resolve_head() == tip
    assert "Pull result: fast-forward" in capsys.readouterr().out


def test_push_set_upstream_uses_resolved_default(tmp_path, monkeypatch, capsys):
    repo = Repository.init(str(tmp_path / "repo"))
    tip = _commit(repo, "a.txt", "one")
    repo.add_remote("origin", "https://example.invalid/origin.git")
    repo.add_remote("publish", "https://example.invalid/publish.git")
    repo.config_set("remote", "pushDefault", "publish")

    calls = []

    def fake_push(self, remote="origin", force=False):
        calls.append((remote, force))
        return {
            "status": "pushed",
            "remote": remote,
            "branch": self.refs.current_branch(),
            "sha": self.refs.resolve_head(),
            "objects": 1,
        }

    monkeypatch.setattr(Repository, "push", fake_push)
    monkeypatch.chdir(repo.worktree)
    assert run_push(["-u"]) == 0
    reopened = Repository(str(repo.worktree))
    assert calls == [("publish", False)]
    assert reopened.config_get("branch", "main.remote") == "publish"
    assert reopened.config_get("branch", "main.merge") == "refs/heads/main"
    assert reopened.refs.resolve_head() == tip
    assert "publish/main" in capsys.readouterr().out
