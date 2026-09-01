"""Phase413 regressions for ``branch <name> @{-N}``."""

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
    base = repo.commit("base", author_name="Phase413", author_email="phase413@example.invalid")
    repo.refs.set_branch("topic", base, message="branch: topic")
    repo.checkout("topic")
    (repo.worktree / "f.txt").write_text("topic\n", encoding="utf-8")
    repo.add(["f.txt"])
    topic = repo.commit("topic", author_name="Phase413", author_email="phase413@example.invalid")
    repo.checkout("main")
    return repo, base, topic


def test_branch_previous_selector_creates_without_checkout(tmp_path: Path, monkeypatch, capsys) -> None:
    repo, base, topic = _seed_repo(tmp_path / "repo")
    capsys.readouterr()
    monkeypatch.chdir(repo.worktree)

    assert run_branch_previous(["new", "@{-1}"]) == 0

    reopened = Repository(str(repo.worktree))
    assert reopened.refs.get_branch("new") == topic
    assert reopened.refs.current_branch() == "main"
    assert reopened.refs.resolve_head() == base
    assert len(topic) == 64
    assert capsys.readouterr().out == "Created branch 'new'.\n"


def test_branch_previous_detached_oid_preserves_sha256(tmp_path: Path, monkeypatch, capsys) -> None:
    repo, base, _ = _seed_repo(tmp_path / "repo")
    capsys.readouterr()
    repo.checkout(base)
    repo.checkout("main")
    monkeypatch.chdir(repo.worktree)

    assert run_branch_previous(["from-detached", "@{-1}"]) == 0
    reopened = Repository(str(repo.worktree))
    assert reopened.refs.get_branch("from-detached") == base
    assert len(base) == 64
    assert reopened.refs.current_branch() == "main"


def test_branch_previous_without_history_fails_before_creation(tmp_path: Path, monkeypatch) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    (repo.worktree / "f.txt").write_text("base\n", encoding="utf-8")
    repo.add(["f.txt"])
    repo.commit("base", author_name="Phase413", author_email="phase413@example.invalid")
    monkeypatch.chdir(repo.worktree)

    with pytest.raises(ValueError):
        run_branch_previous(["new", "@{-1}"])
    assert repo.refs.get_branch("new") is None
    assert repo.refs.current_branch() == "main"


def test_application_routes_only_branch_previous_selectors(monkeypatch) -> None:
    calls: list[tuple[str, list[str]]] = []

    def focused(argv):
        calls.append(("focused", list(argv)))
        return 0

    def legacy():
        calls.append(("legacy", []))

    monkeypatch.setattr(application, "run_branch_previous", focused)
    monkeypatch.setattr(application, "launcher_main", legacy)

    monkeypatch.setattr(sys, "argv", ["pygit", "branch", "new", "@{-1}"])
    application.main()
    assert calls == [("focused", ["new", "@{-1}"])]

    calls.clear()
    monkeypatch.setattr(sys, "argv", ["pygit", "branch", "new", "-"])
    application.main()
    assert calls == [("legacy", [])]

    calls.clear()
    monkeypatch.setattr(sys, "argv", ["pygit", "branch", "new", "main"])
    application.main()
    assert calls == [("legacy", [])]


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    git = shutil.which("git")
    if git is None:
        pytest.skip("native git is unavailable")
    return subprocess.run([git, *args], cwd=str(cwd) if cwd else None, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def test_native_git_and_pygit_branch_previous_match(tmp_path: Path, monkeypatch, capsys) -> None:
    native = tmp_path / "native"
    init = _git("init", "-q", "-b", "main", "--object-format=sha256", str(native))
    if init.returncode != 0:
        pytest.skip("native git does not support SHA-256 repositories")
    assert _git("config", "user.name", "Phase413", cwd=native).returncode == 0
    assert _git("config", "user.email", "phase413@example.invalid", cwd=native).returncode == 0
    (native / "f.txt").write_text("base\n", encoding="utf-8")
    assert _git("add", "f.txt", cwd=native).returncode == 0
    assert _git("commit", "-q", "-m", "base", cwd=native).returncode == 0
    assert _git("checkout", "-q", "-b", "topic", cwd=native).returncode == 0
    (native / "f.txt").write_text("topic\n", encoding="utf-8")
    assert _git("commit", "-qam", "topic", cwd=native).returncode == 0
    assert _git("checkout", "-q", "main", cwd=native).returncode == 0
    assert _git("branch", "new", "@{-1}", cwd=native).returncode == 0
    native_new = _git("rev-parse", "new", cwd=native)
    native_topic = _git("rev-parse", "topic", cwd=native)
    native_head = _git("symbolic-ref", "--short", "HEAD", cwd=native)

    repo, _, topic = _seed_repo(tmp_path / "pygit")
    capsys.readouterr()
    monkeypatch.chdir(repo.worktree)
    assert run_branch_previous(["new", "@{-1}"]) == 0
    reopened = Repository(str(repo.worktree))

    assert native_new.stdout == native_topic.stdout
    assert len(native_new.stdout.strip()) == 64
    assert reopened.refs.get_branch("new") == topic
    assert reopened.refs.current_branch() + "\n" == native_head.stdout
