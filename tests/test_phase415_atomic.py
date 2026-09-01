"""Phase415 fail-closed and destination-config compatibility regressions."""

from __future__ import annotations

from pathlib import Path

import pytest

import pygit.branch_move_previous_cli as phase415
from pygit.branch_move_previous_cli import run_branch_move_previous
from pygit.repo import Repository


def _seed_repo(path: Path) -> tuple[Repository, str, str]:
    repo = Repository.init(str(path))
    (repo.worktree / "f.txt").write_text("base\n", encoding="utf-8")
    repo.add(["f.txt"])
    base = repo.commit(
        "base",
        author_name="Phase415",
        author_email="phase415-atomic@example.invalid",
    )
    repo.refs.set_branch("topic", base, message="branch: topic")
    repo.checkout("topic")
    (repo.worktree / "f.txt").write_text("topic\n", encoding="utf-8")
    repo.add(["f.txt"])
    topic = repo.commit(
        "topic",
        author_name="Phase415",
        author_email="phase415-atomic@example.invalid",
    )
    repo.checkout("main")
    return repo, base, topic


def test_destination_config_key_wins_while_noncolliding_source_keys_move(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _, topic = _seed_repo(tmp_path / "repo")
    repo.config_set("branch", "topic.remote", "origin")
    repo.config_set("branch", "topic.merge", "refs/heads/topic")
    repo.config_set("branch", "renamed.remote", "other")
    monkeypatch.chdir(repo.worktree)

    assert run_branch_move_previous(["-m", "@{-1}", "renamed"]) == 0

    reopened = Repository(str(repo.worktree))
    assert reopened.refs.get_branch("topic") is None
    assert reopened.refs.get_branch("renamed") == topic
    # Git can preserve duplicate config values here. Pygit's scalar config
    # model preserves the destination-visible value and moves non-colliding keys.
    assert reopened.config_get("branch", "renamed.remote") == "other"
    assert reopened.config_get("branch", "renamed.merge") == "refs/heads/topic"
    assert reopened.config_get("branch", "topic.remote") is None
    assert reopened.config_get("branch", "topic.merge") is None


def test_partial_move_failure_restores_config_refs_head_reflogs_and_packed_refs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, base, topic = _seed_repo(tmp_path / "repo")
    repo.config_set("branch", "topic.remote", "origin")
    repo.config_set("branch", "topic.merge", "refs/heads/topic")

    paths = phase415._rename_mutation_paths(repo, "topic", "renamed")
    before = phase415._snapshot_paths(paths)
    real_move_ref = phase415._move_branch_ref

    def move_then_fail(repo_arg: Repository, old: str, new: str) -> None:
        real_move_ref(repo_arg, old, new)
        raise OSError("injected failure after ref move")

    monkeypatch.setattr(phase415, "_move_branch_ref", move_then_fail)

    with pytest.raises(OSError, match="injected failure"):
        phase415._move_branch_atomically(repo, "topic", "renamed")

    after = phase415._snapshot_paths(paths)
    assert after == before

    reopened = Repository(str(repo.worktree))
    assert reopened.refs.get_branch("topic") == topic
    assert reopened.refs.get_branch("renamed") is None
    assert reopened.refs.current_branch() == "main"
    assert reopened.refs.resolve_head() == base
    assert reopened.config_get("branch", "topic.remote") == "origin"
    assert reopened.config_get("branch", "topic.merge") == "refs/heads/topic"
    assert reopened.config_get("branch", "renamed.remote") is None
    assert reopened.refs.read_reflog("refs/heads/topic")[0].new_sha == topic
