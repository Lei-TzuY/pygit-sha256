"""Phase417 regressions for ``branch -c/-C @{-N} <new>``."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import pygit.application as application
from pygit.branch_copy_previous_cli import run_branch_copy_previous
from pygit.repo import Repository


def _seed_repo(path: Path) -> tuple[Repository, str, str]:
    repo = Repository.init(str(path))
    (repo.worktree / "f.txt").write_text("base\n", encoding="utf-8")
    repo.add(["f.txt"])
    base = repo.commit("base", author_name="Phase417", author_email="phase417@example.invalid")
    repo.refs.set_branch("topic", base, message="branch: topic")
    repo.checkout("topic")
    (repo.worktree / "f.txt").write_text("topic\n", encoding="utf-8")
    repo.add(["f.txt"])
    topic = repo.commit("topic", author_name="Phase417", author_email="phase417@example.invalid")
    repo.checkout("main")
    return repo, base, topic


def test_branch_copy_previous_copies_tip_config_and_reflog_history(tmp_path: Path, monkeypatch, capsys) -> None:
    repo, base, topic = _seed_repo(tmp_path / "repo")
    repo.config_set("branch", "topic.remote", "origin")
    repo.config_set("branch", "topic.merge", "refs/heads/topic")
    repo.config_set("branch", "topic.description", "copied description")
    source_log = repo.reflog("refs/heads/topic")
    capsys.readouterr()
    monkeypatch.chdir(repo.worktree)

    assert run_branch_copy_previous(["-c", "@{-1}", "copy"]) == 0

    reopened = Repository(str(repo.worktree))
    assert reopened.refs.get_branch("copy") == topic
    assert reopened.refs.current_branch() == "main"
    assert reopened.refs.resolve_head() == base
    assert reopened.config_get("branch", "copy.remote") == "origin"
    assert reopened.config_get("branch", "copy.merge") == "refs/heads/topic"
    assert reopened.config_get("branch", "copy.description") == "copied description"
    copied_log = reopened.reflog("refs/heads/copy")
    assert copied_log[0].old_sha == topic
    assert copied_log[0].new_sha == topic
    assert copied_log[0].message == "Branch: copied refs/heads/topic to refs/heads/copy"
    assert [entry.message for entry in copied_log[1:]] == [entry.message for entry in source_log]
    assert capsys.readouterr().out == ""


def test_branch_copy_long_form_matches_short_form(tmp_path: Path, monkeypatch) -> None:
    repo, _base, topic = _seed_repo(tmp_path / "repo")
    monkeypatch.chdir(repo.worktree)

    assert run_branch_copy_previous(["--copy", "@{-1}", "copy"]) == 0
    assert Repository(str(repo.worktree)).refs.get_branch("copy") == topic


def test_branch_copy_force_overwrites_tip_but_preserves_existing_destination_config(tmp_path: Path, monkeypatch) -> None:
    repo, base, topic = _seed_repo(tmp_path / "repo")
    repo.config_set("branch", "topic.remote", "origin")
    repo.config_set("branch", "topic.merge", "refs/heads/topic")
    repo.config_set("branch", "topic.description", "source description")
    repo.refs.set_branch("dest", base, message="branch: dest")
    repo.config_set("branch", "dest.remote", "other")
    repo.config_set("branch", "dest.description", "destination description")
    monkeypatch.chdir(repo.worktree)

    assert run_branch_copy_previous(["-C", "@{-1}", "dest"]) == 0

    reopened = Repository(str(repo.worktree))
    assert reopened.refs.get_branch("dest") == topic
    assert reopened.config_get("branch", "dest.remote") == "other"
    assert reopened.config_get("branch", "dest.description") == "destination description"
    assert reopened.config_get("branch", "dest.merge") == "refs/heads/topic"
    assert reopened.reflog("refs/heads/dest")[0].message == (
        "Branch: copied refs/heads/topic to refs/heads/dest"
    )


def test_branch_copy_force_long_forms_are_supported(tmp_path: Path, monkeypatch) -> None:
    repo, base, topic = _seed_repo(tmp_path / "repo")
    repo.refs.set_branch("one", base, message="branch: one")
    repo.refs.set_branch("two", base, message="branch: two")
    monkeypatch.chdir(repo.worktree)

    assert run_branch_copy_previous(["--copy", "--force", "@{-1}", "one"]) == 0
    assert run_branch_copy_previous(["--force", "--copy", "@{-1}", "two"]) == 0
    reopened = Repository(str(repo.worktree))
    assert reopened.refs.get_branch("one") == topic
    assert reopened.refs.get_branch("two") == topic


def test_branch_copy_existing_without_force_is_rejected_before_mutation(tmp_path: Path, monkeypatch) -> None:
    repo, base, _topic = _seed_repo(tmp_path / "repo")
    repo.refs.set_branch("dest", base, message="branch: dest")
    before_log = (repo.pygit_dir / "logs" / "refs" / "heads" / "dest").read_bytes()
    monkeypatch.chdir(repo.worktree)

    with pytest.raises(ValueError, match="already exists"):
        run_branch_copy_previous(["-c", "@{-1}", "dest"])

    reopened = Repository(str(repo.worktree))
    assert reopened.refs.get_branch("dest") == base
    assert (repo.pygit_dir / "logs" / "refs" / "heads" / "dest").read_bytes() == before_log


def test_branch_copy_force_refuses_checked_out_destination(tmp_path: Path, monkeypatch) -> None:
    repo, base, _topic = _seed_repo(tmp_path / "repo")
    monkeypatch.chdir(repo.worktree)

    with pytest.raises(ValueError, match="current worktree"):
        run_branch_copy_previous(["-C", "@{-1}", "main"])

    assert Repository(str(repo.worktree)).refs.get_branch("main") == base


def test_branch_copy_previous_detached_selector_is_not_a_branch(tmp_path: Path, monkeypatch) -> None:
    repo, base, topic = _seed_repo(tmp_path / "repo")
    repo.refs.set_head_detached(topic, message=f"checkout: moving to {topic}")
    repo.checkout("main")
    monkeypatch.chdir(repo.worktree)

    with pytest.raises(ValueError, match="no branch named"):
        run_branch_copy_previous(["-c", "@{-1}", "copy"])

    reopened = Repository(str(repo.worktree))
    assert reopened.refs.get_branch("copy") is None
    assert reopened.refs.current_branch() == "main"
    assert reopened.refs.resolve_head() == base


def test_branch_copy_onto_itself_appends_copy_event_without_moving_head(tmp_path: Path, monkeypatch) -> None:
    repo, _base, topic = _seed_repo(tmp_path / "repo")
    repo.checkout("topic")
    repo.checkout("main")
    monkeypatch.chdir(repo.worktree)
    assert run_branch_copy_previous(["-c", "@{-1}", "topic"]) == 0

    reopened = Repository(str(repo.worktree))
    assert reopened.refs.get_branch("topic") == topic
    assert reopened.refs.current_branch() == "main"
    assert reopened.reflog("refs/heads/topic")[0].message == (
        "Branch: copied refs/heads/topic to refs/heads/topic"
    )


def test_branch_copy_rejects_invalid_destination_ref(tmp_path: Path, monkeypatch) -> None:
    repo, _base, _topic = _seed_repo(tmp_path / "repo")
    monkeypatch.chdir(repo.worktree)
    with pytest.raises(ValueError):
        run_branch_copy_previous(["-c", "@{-1}", "bad..name"])
    assert Repository(str(repo.worktree)).refs.get_branch("bad..name") is None


def test_application_routes_only_previous_branch_copy_shapes(monkeypatch) -> None:
    calls: list[tuple[str, list[str]]] = []

    def focused(argv):
        calls.append(("focused", list(argv)))
        return 0

    def legacy():
        calls.append(("legacy", []))

    monkeypatch.setattr(application, "run_branch_copy_previous", focused)
    monkeypatch.setattr(application, "launcher_main", legacy)

    for argv in (
        ["pygit", "branch", "-c", "@{-1}", "copy"],
        ["pygit", "branch", "--copy", "@{-2}", "copy"],
        ["pygit", "branch", "-C", "@{-1}", "copy"],
        ["pygit", "branch", "--copy", "--force", "@{-1}", "copy"],
        ["pygit", "branch", "--force", "--copy", "@{-1}", "copy"],
    ):
        calls.clear()
        monkeypatch.setattr(sys, "argv", argv)
        application.main()
        assert calls == [("focused", argv[2:])]

    for argv in (
        ["pygit", "branch", "-c", "-", "copy"],
        ["pygit", "branch", "-c", "main", "copy"],
        ["pygit", "branch", "-c", "@{-1}", "copy", "extra"],
    ):
        calls.clear()
        monkeypatch.setattr(sys, "argv", argv)
        application.main()
        assert calls == [("legacy", [])]


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    git = shutil.which("git")
    if git is None:
        pytest.skip("native git is unavailable")
    return subprocess.run(
        [git, *args],
        cwd=str(cwd) if cwd else None,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_native_git_and_pygit_branch_copy_previous_match(tmp_path: Path, monkeypatch, capsys) -> None:
    native = tmp_path / "native"
    init = _git("init", "-q", "-b", "main", "--object-format=sha256", str(native))
    if init.returncode != 0:
        pytest.skip("native git does not support SHA-256 repositories")
    assert _git("config", "user.name", "Phase417", cwd=native).returncode == 0
    assert _git("config", "user.email", "phase417@example.invalid", cwd=native).returncode == 0
    (native / "f.txt").write_text("base\n", encoding="utf-8")
    assert _git("add", "f.txt", cwd=native).returncode == 0
    assert _git("commit", "-q", "-m", "base", cwd=native).returncode == 0
    assert _git("checkout", "-q", "-b", "topic", cwd=native).returncode == 0
    (native / "f.txt").write_text("topic\n", encoding="utf-8")
    assert _git("commit", "-qam", "topic", cwd=native).returncode == 0
    assert _git("config", "branch.topic.remote", "origin", cwd=native).returncode == 0
    assert _git("config", "branch.topic.merge", "refs/heads/topic", cwd=native).returncode == 0
    assert _git("config", "branch.topic.description", "native description", cwd=native).returncode == 0
    assert _git("checkout", "-q", "main", cwd=native).returncode == 0

    native_copy = _git("branch", "-c", "@{-1}", "copy", cwd=native)
    assert native_copy.returncode == 0
    assert native_copy.stdout == ""
    native_copy_oid = _git("rev-parse", "copy", cwd=native).stdout.strip()
    native_topic_oid = _git("rev-parse", "topic", cwd=native).stdout.strip()
    native_head = _git("symbolic-ref", "--short", "HEAD", cwd=native).stdout.strip()
    native_reflog = _git("reflog", "-1", "--format=%gs", "refs/heads/copy", cwd=native).stdout.strip()
    native_remote = _git("config", "branch.copy.remote", cwd=native).stdout.strip()
    native_merge = _git("config", "branch.copy.merge", cwd=native).stdout.strip()
    native_description = _git("config", "branch.copy.description", cwd=native).stdout.strip()

    repo, _base, topic = _seed_repo(tmp_path / "pygit")
    repo.config_set("branch", "topic.remote", "origin")
    repo.config_set("branch", "topic.merge", "refs/heads/topic")
    repo.config_set("branch", "topic.description", "native description")
    capsys.readouterr()
    monkeypatch.chdir(repo.worktree)
    assert run_branch_copy_previous(["-c", "@{-1}", "copy"]) == 0
    reopened = Repository(str(repo.worktree))

    assert native_copy_oid == native_topic_oid
    assert len(native_copy_oid) == 64
    assert reopened.refs.get_branch("copy") == topic
    assert len(topic) == 64
    assert reopened.refs.current_branch() == native_head == "main"
    assert reopened.reflog("refs/heads/copy")[0].message == native_reflog
    assert reopened.config_get("branch", "copy.remote") == native_remote
    assert reopened.config_get("branch", "copy.merge") == native_merge
    assert reopened.config_get("branch", "copy.description") == native_description
    assert capsys.readouterr().out == native_copy.stdout
