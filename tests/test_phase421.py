"""Phase421 fail-closed branch-copy regressions."""

from __future__ import annotations

from pathlib import Path

import pytest

import pygit.branch_copy_previous_cli as branch_copy
from pygit.repo import Repository


def _seed_repo(path: Path) -> tuple[Repository, str, str]:
    repo = Repository.init(str(path))
    (repo.worktree / "f.txt").write_text("base\n", encoding="utf-8")
    repo.add(["f.txt"])
    base = repo.commit("base", author_name="Phase421", author_email="phase421@example.invalid")
    repo.refs.set_branch("topic", base, message="branch: topic")
    repo.checkout("topic")
    (repo.worktree / "f.txt").write_text("topic\n", encoding="utf-8")
    repo.add(["f.txt"])
    topic = repo.commit("topic", author_name="Phase421", author_email="phase421@example.invalid")
    repo.checkout("main")
    return repo, base, topic


def _bytes_or_none(path: Path):
    return path.read_bytes() if path.is_file() else None


def test_force_copy_late_config_failure_restores_existing_destination_exactly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, base, topic = _seed_repo(tmp_path / "repo")
    repo.refs.set_branch("dest", base, message="branch: dest")
    repo.config_set("branch", "topic.remote", "origin")
    repo.config_set("branch", "topic.merge", "refs/heads/topic")
    repo.config_set("branch", "dest.remote", "existing")
    monkeypatch.chdir(repo.worktree)

    config = repo.pygit_dir / "config"
    packed = repo.pygit_dir / "packed-refs"
    dest_ref = branch_copy._branch_storage_path(repo, "dest")
    dest_log = repo.refs._log_path("refs/heads/dest")
    before = {
        config: _bytes_or_none(config),
        packed: _bytes_or_none(packed),
        dest_ref: _bytes_or_none(dest_ref),
        dest_log: _bytes_or_none(dest_log),
    }

    real_copy_config = branch_copy._copy_branch_config

    def copy_then_fail(current_repo, source: str, destination: str) -> None:
        real_copy_config(current_repo, source, destination)
        raise OSError("injected post-config copy failure")

    monkeypatch.setattr(branch_copy, "_copy_branch_config", copy_then_fail)

    with pytest.raises(OSError, match="post-config copy failure"):
        branch_copy.run_branch_copy_previous(["-C", "@{-1}", "dest"])

    reopened = Repository(str(repo.worktree))
    assert reopened.refs.get_branch("topic") == topic
    assert reopened.refs.get_branch("dest") == base
    assert reopened.refs.current_branch() == "main"
    assert reopened.config_get("branch", "dest.remote") == "existing"
    assert reopened.config_get("branch", "dest.merge") is None
    for path, content in before.items():
        assert _bytes_or_none(path) == content


def test_new_destination_late_failure_leaves_no_ref_reflog_or_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _base, topic = _seed_repo(tmp_path / "repo")
    repo.config_set("branch", "topic.remote", "origin")
    repo.config_set("branch", "topic.merge", "refs/heads/topic")
    monkeypatch.chdir(repo.worktree)

    config_before = (repo.pygit_dir / "config").read_bytes()
    real_copy_config = branch_copy._copy_branch_config

    def copy_then_fail(current_repo, source: str, destination: str) -> None:
        real_copy_config(current_repo, source, destination)
        raise RuntimeError("injected late copy failure")

    monkeypatch.setattr(branch_copy, "_copy_branch_config", copy_then_fail)

    with pytest.raises(RuntimeError, match="late copy failure"):
        branch_copy.run_branch_copy_previous(["-c", "@{-1}", "copy"])

    reopened = Repository(str(repo.worktree))
    assert reopened.refs.get_branch("topic") == topic
    assert reopened.refs.get_branch("copy") is None
    assert reopened.config_get("branch", "copy.remote") is None
    assert reopened.config_get("branch", "copy.merge") is None
    assert (repo.pygit_dir / "config").read_bytes() == config_before
    assert not branch_copy._branch_storage_path(repo, "copy").exists()
    assert not repo.refs._log_path("refs/heads/copy").exists()


def test_reflog_failure_restores_destination_before_config_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, base, topic = _seed_repo(tmp_path / "repo")
    repo.refs.set_branch("dest", base, message="branch: dest")
    monkeypatch.chdir(repo.worktree)

    dest_ref = branch_copy._branch_storage_path(repo, "dest")
    dest_log = repo.refs._log_path("refs/heads/dest")
    ref_before = dest_ref.read_bytes()
    log_before = dest_log.read_bytes()

    ref_store_type = type(repo.refs)
    real_append = ref_store_type._append_reflog

    def fail_copy_event(self, refname, old_sha, new_sha, message, *, force=False):
        if refname == "refs/heads/dest" and message.startswith("Branch: copied "):
            raise OSError("injected copy reflog failure")
        return real_append(self, refname, old_sha, new_sha, message, force=force)

    monkeypatch.setattr(ref_store_type, "_append_reflog", fail_copy_event)

    with pytest.raises(OSError, match="copy reflog failure"):
        branch_copy.run_branch_copy_previous(["-C", "@{-1}", "dest"])

    reopened = Repository(str(repo.worktree))
    assert reopened.refs.get_branch("topic") == topic
    assert reopened.refs.get_branch("dest") == base
    assert dest_ref.read_bytes() == ref_before
    assert dest_log.read_bytes() == log_before


def test_successful_copy_still_uses_existing_phase417_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _base, topic = _seed_repo(tmp_path / "repo")
    repo.config_set("branch", "topic.remote", "origin")
    monkeypatch.chdir(repo.worktree)

    assert branch_copy.run_branch_copy_previous(["-c", "@{-1}", "copy"]) == 0
    reopened = Repository(str(repo.worktree))
    assert reopened.refs.get_branch("copy") == topic
    assert reopened.config_get("branch", "copy.remote") == "origin"
    assert reopened.reflog("refs/heads/copy")[0].message == (
        "Branch: copied refs/heads/topic to refs/heads/copy"
    )
