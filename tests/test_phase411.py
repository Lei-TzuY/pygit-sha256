"""Phase411 regressions for ``checkout --detach -``."""

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
        author_name="Phase411",
        author_email="phase411@example.invalid",
    )
    repo.refs.set_branch("topic", base, message="branch: topic")
    return repo, base


def test_checkout_detach_dash_uses_previous_branch_tip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo, base = _seed_repo(tmp_path / "repo")
    capsys.readouterr()
    repo.checkout("topic")
    repo.checkout("main")
    monkeypatch.chdir(repo.worktree)

    assert run_checkout_previous(["--detach", "-"]) == 0

    reopened = Repository(str(repo.worktree))
    assert reopened.refs.current_branch() is None
    assert reopened.refs.resolve_head() == base
    assert len(base) == 64
    assert reopened.reflog("HEAD")[0].message == "checkout: moving from main to topic"
    assert capsys.readouterr().out == f"HEAD is now at {base[:12]}\n"


def test_checkout_detach_dash_round_trips_previous_detached_oid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo, base = _seed_repo(tmp_path / "repo")
    capsys.readouterr()
    repo.checkout(base)
    repo.checkout("main")
    monkeypatch.chdir(repo.worktree)

    assert run_checkout_previous(["--detach", "-"]) == 0

    reopened = Repository(str(repo.worktree))
    assert reopened.refs.current_branch() is None
    assert reopened.refs.resolve_head() == base
    assert reopened.reflog("HEAD")[0].message == f"checkout: moving from main to {base}"


def test_application_routes_only_exact_detach_dash_shape(
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

    monkeypatch.setattr(sys, "argv", ["pygit", "checkout", "--detach", "-"])
    application.main()
    assert calls == [("previous", ["--detach", "-"])]

    calls.clear()
    monkeypatch.setattr(sys, "argv", ["pygit", "checkout", "--detach", "-", "extra"])
    application.main()
    assert calls == [("legacy", [])]

    calls.clear()
    monkeypatch.setattr(sys, "argv", ["pygit", "checkout", "--detach", "main"])
    application.main()
    assert calls == [("legacy", [])]


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


def test_native_git_and_pygit_detach_dash_match_state_and_reflog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    native = tmp_path / "native"
    init = _git("init", "-q", "-b", "main", "--object-format=sha256", str(native))
    if init.returncode != 0:
        pytest.skip("native git does not support SHA-256 repositories")
    assert _git("config", "user.name", "Phase411", cwd=native).returncode == 0
    assert _git("config", "user.email", "phase411@example.invalid", cwd=native).returncode == 0
    (native / "f.txt").write_text("base\n", encoding="utf-8")
    assert _git("add", "f.txt", cwd=native).returncode == 0
    assert _git("commit", "-q", "-m", "base", cwd=native).returncode == 0
    assert _git("checkout", "-q", "-b", "topic", cwd=native).returncode == 0
    assert _git("checkout", "-q", "main", cwd=native).returncode == 0
    assert _git("checkout", "--detach", "-", cwd=native).returncode == 0

    native_head = _git("rev-parse", "HEAD", cwd=native)
    native_topic = _git("rev-parse", "topic", cwd=native)
    native_symbolic = _git("symbolic-ref", "-q", "--short", "HEAD", cwd=native)
    native_message = _git("reflog", "-1", "--format=%gs", "HEAD", cwd=native)

    repo, base = _seed_repo(tmp_path / "pygit")
    capsys.readouterr()
    repo.checkout("topic")
    repo.checkout("main")
    monkeypatch.chdir(repo.worktree)
    assert run_checkout_previous(["--detach", "-"]) == 0

    reopened = Repository(str(repo.worktree))
    pygit_head = reopened.refs.resolve_head()
    pygit_topic = reopened.refs.get_branch("topic")

    assert native_symbolic.returncode != 0
    assert reopened.refs.current_branch() is None
    assert native_head.stdout == native_topic.stdout
    assert pygit_head == pygit_topic == base
    assert native_head.stdout.strip() and len(native_head.stdout.strip()) == 64
    assert pygit_head is not None and len(pygit_head) == 64
    assert reopened.reflog("HEAD")[0].message + "\n" == native_message.stdout
