"""Phase414 regressions for ``branch -f <name> @{-N}``."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import pygit.application as application
from pygit.branch_previous_cli import run_branch_previous
from pygit.repo import Repository


def _seed_repo(path: Path) -> tuple[Repository, str, str]:
    repo = Repository.init(str(path))
    (repo.worktree / "f.txt").write_text("base\n", encoding="utf-8")
    repo.add(["f.txt"])
    base = repo.commit("base", author_name="Phase414", author_email="phase414@example.invalid")
    repo.refs.set_branch("topic", base, message="branch: topic")
    repo.checkout("topic")
    (repo.worktree / "f.txt").write_text("topic\n", encoding="utf-8")
    repo.add(["f.txt"])
    topic = repo.commit("topic", author_name="Phase414", author_email="phase414@example.invalid")
    repo.checkout("main")
    return repo, base, topic


def test_branch_force_previous_resets_existing_branch_without_checkout(tmp_path: Path, monkeypatch, capsys) -> None:
    repo, base, topic = _seed_repo(tmp_path / "repo")
    repo.refs.set_branch("spare", base, message="branch: spare")
    capsys.readouterr()
    monkeypatch.chdir(repo.worktree)

    assert run_branch_previous(["-f", "spare", "@{-1}"]) == 0

    reopened = Repository(str(repo.worktree))
    assert reopened.refs.get_branch("spare") == topic
    assert reopened.refs.current_branch() == "main"
    assert reopened.refs.resolve_head() == base
    assert len(topic) == 64
    assert capsys.readouterr().out == ""


def test_branch_force_long_option_matches_short_option(tmp_path: Path, monkeypatch) -> None:
    repo, base, topic = _seed_repo(tmp_path / "repo")
    repo.refs.set_branch("spare", base, message="branch: spare")
    monkeypatch.chdir(repo.worktree)

    assert run_branch_previous(["--force", "spare", "@{-1}"]) == 0
    assert Repository(str(repo.worktree)).refs.get_branch("spare") == topic


def test_branch_previous_existing_without_force_is_rejected_before_mutation(tmp_path: Path, monkeypatch) -> None:
    repo, base, _ = _seed_repo(tmp_path / "repo")
    repo.refs.set_branch("spare", base, message="branch: spare")
    monkeypatch.chdir(repo.worktree)

    with pytest.raises(ValueError, match="already exists"):
        run_branch_previous(["spare", "@{-1}"])

    reopened = Repository(str(repo.worktree))
    assert reopened.refs.get_branch("spare") == base
    assert reopened.refs.current_branch() == "main"


def test_branch_force_refuses_checked_out_branch(tmp_path: Path, monkeypatch) -> None:
    repo, base, _ = _seed_repo(tmp_path / "repo")
    monkeypatch.chdir(repo.worktree)

    with pytest.raises(ValueError, match="checked-out branch"):
        run_branch_previous(["-f", "main", "@{-1}"])

    reopened = Repository(str(repo.worktree))
    assert reopened.refs.get_branch("main") == base
    assert reopened.refs.current_branch() == "main"


def test_application_routes_only_force_previous_selector_shape(monkeypatch) -> None:
    calls: list[tuple[str, list[str]]] = []

    def focused(argv):
        calls.append(("focused", list(argv)))
        return 0

    def legacy():
        calls.append(("legacy", []))

    monkeypatch.setattr(application, "run_branch_previous", focused)
    monkeypatch.setattr(application, "launcher_main", legacy)

    monkeypatch.setattr(sys, "argv", ["pygit", "branch", "-f", "spare", "@{-1}"])
    application.main()
    assert calls == [("focused", ["-f", "spare", "@{-1}"])]

    calls.clear()
    monkeypatch.setattr(sys, "argv", ["pygit", "branch", "--force", "spare", "@{-2}"])
    application.main()
    assert calls == [("focused", ["--force", "spare", "@{-2}"])]

    calls.clear()
    monkeypatch.setattr(sys, "argv", ["pygit", "branch", "-f", "spare", "main"])
    application.main()
    assert calls == [("legacy", [])]

    calls.clear()
    monkeypatch.setattr(sys, "argv", ["pygit", "branch", "-f", "spare", "-"])
    application.main()
    assert calls == [("legacy", [])]


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    git = shutil.which("git")
    if git is None:
        pytest.skip("native git is unavailable")
    return subprocess.run([git, *args], cwd=str(cwd) if cwd else None, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def test_native_git_and_pygit_branch_force_previous_match(tmp_path: Path, monkeypatch, capsys) -> None:
    native = tmp_path / "native"
    init = _git("init", "-q", "-b", "main", "--object-format=sha256", str(native))
    if init.returncode != 0:
        pytest.skip("native git does not support SHA-256 repositories")
    assert _git("config", "user.name", "Phase414", cwd=native).returncode == 0
    assert _git("config", "user.email", "phase414@example.invalid", cwd=native).returncode == 0
    (native / "f.txt").write_text("base\n", encoding="utf-8")
    assert _git("add", "f.txt", cwd=native).returncode == 0
    assert _git("commit", "-q", "-m", "base", cwd=native).returncode == 0
    assert _git("branch", "spare", cwd=native).returncode == 0
    assert _git("checkout", "-q", "-b", "topic", cwd=native).returncode == 0
    (native / "f.txt").write_text("topic\n", encoding="utf-8")
    assert _git("commit", "-qam", "topic", cwd=native).returncode == 0
    assert _git("checkout", "-q", "main", cwd=native).returncode == 0

    native_force = _git("branch", "-f", "spare", "@{-1}", cwd=native)
    assert native_force.returncode == 0
    assert native_force.stdout == ""
    native_spare = _git("rev-parse", "spare", cwd=native)
    native_topic = _git("rev-parse", "topic", cwd=native)
    native_head = _git("symbolic-ref", "--short", "HEAD", cwd=native)
    native_reflog = _git("reflog", "-1", "--format=%gs", "refs/heads/spare", cwd=native)
    assert native_reflog.stdout.strip() == "branch: Reset to @{-1}"

    repo, base, topic = _seed_repo(tmp_path / "pygit")
    repo.refs.set_branch("spare", base, message="branch: spare")
    capsys.readouterr()
    monkeypatch.chdir(repo.worktree)
    assert run_branch_previous(["-f", "spare", "@{-1}"]) == 0
    reopened = Repository(str(repo.worktree))

    assert native_spare.stdout == native_topic.stdout
    assert len(native_spare.stdout.strip()) == 64
    assert reopened.refs.get_branch("spare") == topic
    assert len(topic) == 64
    assert reopened.refs.current_branch() + "\n" == native_head.stdout
    assert capsys.readouterr().out == native_force.stdout
