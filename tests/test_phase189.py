from __future__ import annotations

import pytest

from pygit.fetch_atomic import atomic_ref_updates
from pygit.fetch_cli import run_fetch
from pygit.repo import Repository


def _commit(repo: Repository, name: str = "a.txt", text: str = "one") -> str:
    path = repo.worktree / name
    path.write_text(text, encoding="utf-8")
    repo.add([name])
    return repo.commit(text, author_name="Test", author_email="test@example.com")


def test_atomic_ref_scope_rolls_back_loose_refs_and_reflogs(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    first = _commit(repo)
    repo.add_remote("origin", "https://example.invalid/repo.git")
    repo.refs.set_remote("origin", "main", first)
    before_log = repo.refs.read_reflog("refs/heads/main")

    with pytest.raises(RuntimeError, match="boom"):
        with atomic_ref_updates(repo):
            second = _commit(repo, "b.txt", "two")
            repo.refs.set_remote("origin", "main", second)
            repo.refs.set_tag("temporary", second)
            raise RuntimeError("boom")

    reopened = Repository(str(repo.worktree))
    assert reopened.refs.get_branch("main") == first
    assert reopened.refs.get_remote("origin", "main") == first
    assert reopened.refs.get_tag("temporary") is None
    assert reopened.refs.read_reflog("refs/heads/main") == before_log


def test_atomic_ref_scope_keeps_successful_updates(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    first = _commit(repo)
    repo.add_remote("origin", "https://example.invalid/repo.git")

    with atomic_ref_updates(repo):
        repo.refs.set_remote("origin", "main", first)
        repo.refs.set_tag("v1", first)

    assert repo.refs.get_remote("origin", "main") == first
    assert repo.refs.get_tag("v1") == first


def test_atomic_scope_restores_packed_refs_file(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    sha = _commit(repo)
    packed = repo.pygit_dir / "packed-refs"
    original = f"# pack-refs with: peeled\n{sha} refs/tags/packed\n"
    packed.write_text(original, encoding="utf-8")

    with pytest.raises(RuntimeError):
        with atomic_ref_updates(repo):
            packed.write_text("", encoding="utf-8")
            repo.refs.set_tag("loose", sha)
            raise RuntimeError("stop")

    assert packed.read_text(encoding="utf-8") == original
    assert repo.refs.get_tag("packed") == sha
    assert repo.refs.get_tag("loose") is None


def test_fetch_atomic_rolls_back_partial_named_remote_updates(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    old = _commit(repo)
    repo.add_remote("origin", "https://example.invalid/repo.git")
    repo.refs.set_remote("origin", "main", old)
    new = "f" * 64

    def failing_fetch(repo_arg, remote="origin", **kwargs):
        repo_arg.refs.set_remote(remote, "main", new)
        repo_arg.refs.set_tag("partial", new)
        raise RuntimeError("simulated update failure")

    monkeypatch.setattr("pygit.fetch_cli.fetch_configured", failing_fetch)
    monkeypatch.chdir(repo.worktree)

    with pytest.raises(RuntimeError, match="simulated update failure"):
        run_fetch(["--atomic", "origin"])

    reopened = Repository(str(repo.worktree))
    assert reopened.refs.get_remote("origin", "main") == old
    assert reopened.refs.get_tag("partial") is None


def test_fetch_without_atomic_keeps_pre_failure_mutation(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    old = _commit(repo)
    repo.add_remote("origin", "https://example.invalid/repo.git")
    repo.refs.set_remote("origin", "main", old)
    new = "e" * 64

    def failing_fetch(repo_arg, remote="origin", **kwargs):
        repo_arg.refs.set_remote(remote, "main", new)
        raise RuntimeError("simulated update failure")

    monkeypatch.setattr("pygit.fetch_cli.fetch_configured", failing_fetch)
    monkeypatch.chdir(repo.worktree)

    with pytest.raises(RuntimeError):
        run_fetch(["origin"])

    assert Repository(str(repo.worktree)).refs.get_remote("origin", "main") == new


def test_fetch_atomic_wraps_direct_url_destination_updates(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    sha = _commit(repo)

    def failing_direct(repo_arg, url, **kwargs):
        repo_arg.refs.set_tag("partial", sha)
        raise RuntimeError("direct failure")

    monkeypatch.setattr("pygit.fetch_cli.fetch_direct_url", failing_direct)
    monkeypatch.chdir(repo.worktree)

    with pytest.raises(RuntimeError, match="direct failure"):
        run_fetch(["--atomic", "https://example.invalid/repo.git", "main:v"])

    assert Repository(str(repo.worktree)).refs.get_tag("partial") is None


@pytest.mark.parametrize(
    "argv",
    [
        ["--atomic", "--multiple", "origin", "backup"],
        ["--atomic", "--all"],
    ],
)
def test_fetch_atomic_rejects_multi_remote_modes(tmp_path, monkeypatch, argv):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.invalid/origin.git")
    repo.add_remote("backup", "https://example.invalid/backup.git")
    monkeypatch.chdir(repo.worktree)

    with pytest.raises(RuntimeError, match="only be used when fetching from one remote"):
        run_fetch(argv)


def test_fetch_atomic_rejects_remote_group(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.invalid/origin.git")
    repo.add_remote("backup", "https://example.invalid/backup.git")
    repo.config_set("remotes", "both", "origin backup")
    monkeypatch.chdir(repo.worktree)

    with pytest.raises(RuntimeError, match="only be used when fetching from one remote"):
        run_fetch(["--atomic", "both"])
