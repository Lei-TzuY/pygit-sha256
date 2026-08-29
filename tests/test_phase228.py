from __future__ import annotations

import pytest

from pygit import promisor_status
from pygit.promisor import update_promisor_state
from pygit.repo import Repository


def _ordinary_commit(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    (repo.worktree / "a.txt").write_text("hello\n", encoding="utf-8")
    repo.add(["a.txt"])
    head = repo.commit("root", author_name="Test", author_email="test@example.com")
    return repo, head


def test_status_prefetches_head_once_when_promises_exist(tmp_path, monkeypatch):
    repo, head = _ordinary_commit(tmp_path)
    update_promisor_state(
        repo.pygit_dir,
        remote="origin",
        filter_spec="blob:none",
        promised={"1" * 40: "blob", "2" * 40: "blob"},
    )
    calls = []

    def fake_prefetch(target_repo, commits):
        calls.append((target_repo, tuple(commits)))
        return set()

    monkeypatch.setattr(promisor_status, "prefetch_history_promises", fake_prefetch)

    result = repo.status()

    assert calls == [(repo, (head,))]
    assert result["staged"] == []
    assert result["unstaged"] == []


def test_status_forwards_ignored_flag_after_prefetch(tmp_path, monkeypatch):
    repo, head = _ordinary_commit(tmp_path)
    (repo.worktree / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (repo.worktree / "ignored.txt").write_text("ignored\n", encoding="utf-8")
    update_promisor_state(
        repo.pygit_dir,
        remote="origin",
        filter_spec="blob:none",
        promised={"3" * 40: "blob"},
    )
    calls = []
    monkeypatch.setattr(
        promisor_status,
        "prefetch_history_promises",
        lambda target_repo, commits: calls.append((target_repo, tuple(commits))) or set(),
    )

    result = repo.status(ignored=True)

    assert calls == [(repo, (head,))]
    assert "ignored.txt" in result["ignored"]


def test_status_without_promises_stays_on_historical_path(tmp_path, monkeypatch):
    repo, _head = _ordinary_commit(tmp_path)
    monkeypatch.setattr(
        promisor_status,
        "prefetch_history_promises",
        lambda *args, **kwargs: pytest.fail("ordinary status must not prefetch"),
    )

    result = repo.status()

    assert result["staged"] == []
    assert result["unstaged"] == []


def test_empty_repository_with_sidecar_promise_does_not_prefetch(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "empty"))
    update_promisor_state(
        repo.pygit_dir,
        remote="origin",
        filter_spec="blob:none",
        promised={"4" * 40: "blob"},
    )
    monkeypatch.setattr(
        promisor_status,
        "prefetch_history_promises",
        lambda *args, **kwargs: pytest.fail("status without HEAD must not prefetch"),
    )

    result = repo.status()

    assert result["branch"] == "main"
    assert result["staged"] == []
    assert result["unstaged"] == []
