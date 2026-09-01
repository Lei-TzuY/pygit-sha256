"""Phase409 regressions for ``checkout -`` previous-checkout shorthand."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import pygit.application as application
from pygit.checkout_previous_cli import run_checkout_previous
from pygit.repo import Repository


def _seed_repo(path: Path) -> tuple[Repository, str]:
    repo = Repository.init(str(path))
    (repo.worktree / "f.txt").write_text("base\n", encoding="utf-8")
    repo.add(["f.txt"])
    base = repo.commit(
        "base",
        author_name="Phase409",
        author_email="phase409@example.invalid",
    )
    repo.refs.set_branch("topic", base, message="branch: topic")
    return repo, base


def test_checkout_dash_switches_to_previous_symbolic_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, _ = _seed_repo(tmp_path / "repo")
    capsys.readouterr()
    repo.checkout("topic")
    repo.checkout("main")
    monkeypatch.chdir(repo.worktree)

    assert run_checkout_previous(["-"]) == 0

    reopened = Repository(str(repo.worktree))
    assert reopened.refs.current_branch() == "topic"
    assert reopened.reflog("HEAD")[0].message == "checkout: moving from main to topic"
    assert capsys.readouterr().out == "Switched to branch 'topic'\n"


def test_checkout_dash_preserves_detached_sha256_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, base = _seed_repo(tmp_path / "repo")
    capsys.readouterr()
    repo.checkout(base)
    repo.checkout("main")
    monkeypatch.chdir(repo.worktree)

    assert run_checkout_previous(["-"]) == 0

    reopened = Repository(str(repo.worktree))
    assert reopened.refs.current_branch() is None
    assert reopened.refs.resolve_head() == base
    assert len(base) == 64
    assert reopened.reflog("HEAD")[0].message == f"checkout: moving from main to {base}"


def test_application_routes_only_exact_checkout_dash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, list[str]]] = []

    def previous(argv):
        calls.append(("previous", list(argv)))
        return 0

    def legacy():
        calls.append(("legacy", []))

    monkeypatch.setattr(application, "run_checkout_previous", previous)
    monkeypatch.setattr(application, "launcher_main", legacy)

    monkeypatch.setattr(sys, "argv", ["pygit", "checkout", "-"])
    application.main()
    assert calls == [("previous", ["-"])]

    calls.clear()
    monkeypatch.setattr(sys, "argv", ["pygit", "checkout", "--detach", "-"])
    application.main()
    assert calls == [("legacy", [])]

    calls.clear()
    monkeypatch.setattr(sys, "argv", ["pygit", "checkout", "--", "-"])
    application.main()
    assert calls == [("legacy", [])]


def test_checkout_dash_without_history_fails_before_head_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _ = _seed_repo(tmp_path / "repo")
    monkeypatch.chdir(repo.worktree)
    before = (repo.gitdir / "HEAD").read_bytes()

    with pytest.raises(ValueError):
        run_checkout_previous(["-"])

    assert (repo.gitdir / "HEAD").read_bytes() == before


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    git = shutil.which("git")
    if git is None:
        pytest.skip("native git is unavailable")
    return subprocess.run(
        [git, *args],
        cwd=str(cwd) if cwd is not None else None,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_native_git_and_pygit_checkout_dash_match_branch_and_reflog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    native = tmp_path / "native"
    init = _git("init", "-q", "-b", "main", "--object-format=sha256", str(native))
    if init.returncode != 0:
        pytest.skip("native git does not support SHA-256 repositories")
    assert _git("config", "user.name", "Phase409", cwd=native).returncode == 0
    assert _git("config", "user.email", "phase409@example.invalid", cwd=native).returncode == 0
    (native / "f.txt").write_text("base\n", encoding="utf-8")
    assert _git("add", "f.txt", cwd=native).returncode == 0
    assert _git("commit", "-q", "-m", "base", cwd=native).returncode == 0
    assert _git("checkout", "-q", "-b", "topic", cwd=native).returncode == 0
    assert _git("checkout", "-q", "main", cwd=native).returncode == 0
    assert _git("checkout", "-q", "-", cwd=native).returncode == 0
    native_branch = _git("symbolic-ref", "--short", "HEAD", cwd=native)
    native_message = _git("reflog", "-1", "--format=%gs", "HEAD", cwd=native)

    repo, _ = _seed_repo(tmp_path / "pygit")
    capsys.readouterr()
    repo.checkout("topic")
    repo.checkout("main")
    monkeypatch.chdir(repo.worktree)
    assert run_checkout_previous(["-"]) == 0

    reopened = Repository(str(repo.worktree))
    assert reopened.refs.current_branch() + "\n" == native_branch.stdout
    assert reopened.reflog("HEAD")[0].message + "\n" == native_message.stdout
