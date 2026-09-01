"""Phase419 integration regressions for previous-checkout porcelain."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import pygit.application as application
from pygit.branch_copy_previous_cli import run_branch_copy_previous
from pygit.branch_move_previous_cli import run_branch_move_previous
from pygit.repo import Repository
from pygit.rev_parse_previous_cli import run_rev_parse_previous


def _seed_repo(path: Path) -> tuple[Repository, str, str]:
    repo = Repository.init(str(path))
    (repo.worktree / "f.txt").write_text("base\n", encoding="utf-8")
    repo.add(["f.txt"])
    base = repo.commit("base", author_name="Phase419", author_email="phase419@example.invalid")
    repo.refs.set_branch("topic", base, message="branch: topic")
    repo.checkout("topic")
    (repo.worktree / "f.txt").write_text("topic\n", encoding="utf-8")
    repo.add(["f.txt"])
    topic = repo.commit("topic", author_name="Phase419", author_email="phase419@example.invalid")
    repo.checkout("main")
    return repo, base, topic


def test_rev_parse_copy_and_force_move_share_one_previous_selector_stack(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    repo, base, topic = _seed_repo(tmp_path / "repo")
    capsys.readouterr()
    monkeypatch.chdir(repo.worktree)

    assert run_rev_parse_previous(["@{-1}"]) == 0
    assert capsys.readouterr().out == topic + "\n"

    assert run_branch_copy_previous(["-c", "@{-1}", "copy"]) == 0
    assert run_rev_parse_previous(["@{-1}"]) == 0
    assert capsys.readouterr().out == topic + "\n"

    assert run_branch_move_previous(["-M", "@{-1}", "moved"]) == 0

    reopened = Repository(str(repo.worktree))
    assert reopened.refs.current_branch() == "main"
    assert reopened.refs.resolve_head() == base
    assert reopened.refs.get_branch("topic") is None
    assert reopened.refs.get_branch("copy") == topic
    assert reopened.refs.get_branch("moved") == topic
    assert len(topic) == 64


def test_application_routes_integrated_previous_selector_commands_without_overlap(monkeypatch) -> None:
    calls: list[tuple[str, list[str]]] = []

    def handler(name):
        def run(argv):
            calls.append((name, list(argv)))
            return 0
        return run

    monkeypatch.setattr(application, "run_rev_parse_previous", handler("rev-parse"))
    monkeypatch.setattr(application, "run_branch_copy_previous", handler("copy"))
    monkeypatch.setattr(application, "run_branch_move_previous", handler("move"))
    monkeypatch.setattr(application, "launcher_main", lambda: calls.append(("legacy", [])))

    cases = (
        (["pygit", "rev-parse", "--abbrev-ref", "@{-1}"], "rev-parse", ["--abbrev-ref", "@{-1}"]),
        (["pygit", "branch", "-c", "@{-1}", "copy"], "copy", ["-c", "@{-1}", "copy"]),
        (["pygit", "branch", "-C", "@{-1}", "copy"], "copy", ["-C", "@{-1}", "copy"]),
        (["pygit", "branch", "-M", "@{-1}", "moved"], "move", ["-M", "@{-1}", "moved"]),
        (["pygit", "branch", "--move", "--force", "@{-1}", "moved"], "move", ["--move", "--force", "@{-1}", "moved"]),
    )
    for argv, name, forwarded in cases:
        calls.clear()
        monkeypatch.setattr(sys, "argv", argv)
        application.main()
        assert calls == [(name, forwarded)]

    for argv in (
        ["pygit", "rev-parse", "--short", "@{-1}"],
        ["pygit", "branch", "-c", "HEAD", "copy"],
        ["pygit", "branch", "-m", "HEAD", "moved"],
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


def test_native_git_accepts_rev_parse_copy_then_force_move_sequence(tmp_path: Path) -> None:
    native = tmp_path / "native"
    init = _git("init", "-q", "-b", "main", "--object-format=sha256", str(native))
    if init.returncode != 0:
        pytest.skip("native git does not support SHA-256 repositories")
    assert _git("config", "user.name", "Phase419", cwd=native).returncode == 0
    assert _git("config", "user.email", "phase419@example.invalid", cwd=native).returncode == 0
    (native / "f.txt").write_text("base\n", encoding="utf-8")
    assert _git("add", "f.txt", cwd=native).returncode == 0
    assert _git("commit", "-q", "-m", "base", cwd=native).returncode == 0
    assert _git("checkout", "-q", "-b", "topic", cwd=native).returncode == 0
    (native / "f.txt").write_text("topic\n", encoding="utf-8")
    assert _git("commit", "-qam", "topic", cwd=native).returncode == 0
    assert _git("checkout", "-q", "main", cwd=native).returncode == 0

    before = _git("rev-parse", "@{-1}", cwd=native)
    assert before.returncode == 0
    topic_oid = before.stdout.strip()
    assert len(topic_oid) == 64

    assert _git("branch", "-c", "@{-1}", "copy", cwd=native).returncode == 0
    after_copy = _git("rev-parse", "@{-1}", cwd=native)
    assert after_copy.returncode == 0
    assert after_copy.stdout.strip() == topic_oid

    assert _git("branch", "-M", "@{-1}", "moved", cwd=native).returncode == 0
    assert _git("rev-parse", "copy", cwd=native).stdout.strip() == topic_oid
    assert _git("rev-parse", "moved", cwd=native).stdout.strip() == topic_oid
    assert _git("show-ref", "--verify", "--quiet", "refs/heads/topic", cwd=native).returncode != 0
    assert _git("symbolic-ref", "--short", "HEAD", cwd=native).stdout.strip() == "main"
