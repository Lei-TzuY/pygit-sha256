from __future__ import annotations

from types import SimpleNamespace

import pytest

from pygit.config import GitConfig
from pygit.push_cli import run_push
from pygit.push_defaults import PushPlan, PushSpec
from pygit.push_groups import remote_group_members
from pygit.repo import Repository


def _commit(repo: Repository) -> str:
    path = repo.worktree / "a.txt"
    path.write_text("A", encoding="utf-8")
    repo.add(["a.txt"])
    return repo.commit("A", author_name="Test", author_email="test@example.com")


def _repo(tmp_path) -> Repository:
    repo = Repository.init(str(tmp_path / "repo"))
    _commit(repo)
    repo.add_remote("r1", "https://example.invalid/r1.git")
    repo.add_remote("r2", "https://example.invalid/r2.git")
    return repo


def _set_group(repo: Repository, name: str, value: str) -> None:
    GitConfig(repo.pygit_dir).set("remotes", name, value)


def test_remote_group_members_preserve_configured_order(tmp_path):
    repo = _repo(tmp_path)
    _set_group(repo, "all-remotes", "r2 r1 r2")

    assert remote_group_members(repo, "all-remotes") == ("r2", "r1", "r2")
    assert remote_group_members(repo, "r1") is None
    assert remote_group_members(repo, None) is None


def test_remote_group_rejects_empty_or_unknown_members(tmp_path):
    repo = _repo(tmp_path)
    _set_group(repo, "empty", "   ")
    with pytest.raises(RuntimeError, match="has no members"):
        remote_group_members(repo, "empty")

    _set_group(repo, "broken", "r1 missing r2")
    with pytest.raises(RuntimeError, match="unknown remote.*missing"):
        remote_group_members(repo, "broken")


def test_group_push_runs_each_member_in_order_with_same_parsed_selection(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _set_group(repo, "all-remotes", "r2 r1")
    observed = []

    def fake_one(repo_obj, args, parser, remote, lease, push_options, follow_tags_enabled):
        observed.append(
            (
                remote,
                tuple(args.refspecs),
                args.force,
                tuple(push_options),
                follow_tags_enabled,
            )
        )
        return 0

    monkeypatch.setattr("pygit.push_cli._run_one_remote", fake_one)
    monkeypatch.chdir(repo.worktree)

    assert run_push(["--force", "--follow-tags", "-o", "ci.skip", "all-remotes", "main:release"]) == 0
    assert observed == [
        ("r2", ("main:release",), True, ("ci.skip",), True),
        ("r1", ("main:release",), True, ("ci.skip",), True),
    ]


def test_group_push_continues_after_member_failure_and_returns_nonzero(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path)
    _set_group(repo, "all-remotes", "r1 r2")
    attempted = []

    def fake_one(repo_obj, args, parser, remote, lease, push_options, follow_tags_enabled):
        attempted.append(remote)
        if remote == "r1":
            raise RuntimeError("rejected")
        return 0

    monkeypatch.setattr("pygit.push_cli._run_one_remote", fake_one)
    monkeypatch.chdir(repo.worktree)

    assert run_push(["all-remotes", "main"]) == 1
    assert attempted == ["r1", "r2"]
    assert "Push to r1 failed: rejected" in capsys.readouterr().err


def test_group_push_rejects_atomic_before_any_member_transport(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _set_group(repo, "all-remotes", "r1 r2")
    attempted = []
    monkeypatch.setattr(
        "pygit.push_cli._run_one_remote",
        lambda *args, **kwargs: attempted.append(args[3]) or 0,
    )
    monkeypatch.chdir(repo.worktree)

    with pytest.raises(SystemExit) as exc:
        run_push(["--atomic", "all-remotes", "main"])
    assert exc.value.code == 2
    assert attempted == []


def test_group_members_evaluate_default_and_mirror_plans_independently(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _set_group(repo, "all-remotes", "r1 r2")
    seen = []

    monkeypatch.setattr(
        "pygit.push_cli.configured_remote_mirror",
        lambda repo_obj, remote: remote == "r2",
    )

    def fake_resolve(repo_obj, remote, refspecs):
        assert remote == "r1"
        return PushPlan(remote, (PushSpec("main", "release"),), "configured")

    monkeypatch.setattr("pygit.push_cli.resolve_push_plan", fake_resolve)
    monkeypatch.setattr(
        "pygit.push_cli.mirror_specs",
        lambda repo_obj, remote: (PushSpec("main", "main", force=True),),
    )

    def fake_push_branch(repo_obj, remote, source, target, **kwargs):
        seen.append((remote, source, target, kwargs["force"]))
        return {
            "status": "pushed",
            "remote": remote,
            "source_branch": source,
            "branch": target,
            "sha": repo_obj.refs.resolve_head(),
            "objects": 1,
        }

    monkeypatch.setattr("pygit.push_cli.push_branch", fake_push_branch)
    monkeypatch.chdir(repo.worktree)

    assert run_push(["all-remotes"]) == 0
    assert seen == [
        ("r1", "main", "release", False),
        ("r2", "main", "main", True),
    ]


def test_group_member_parser_failure_does_not_skip_later_remotes(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _set_group(repo, "all-remotes", "r1 r2")
    attempted = []

    def fake_one(repo_obj, args, parser, remote, lease, push_options, follow_tags_enabled):
        attempted.append(remote)
        if remote == "r1":
            parser.error("member-specific configuration conflict")
        return 0

    monkeypatch.setattr("pygit.push_cli._run_one_remote", fake_one)
    monkeypatch.chdir(repo.worktree)

    assert run_push(["all-remotes", "main"]) == 1
    assert attempted == ["r1", "r2"]
