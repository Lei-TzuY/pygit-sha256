"""Phase416 regressions for ``rev-parse @{-N}`` support."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import pygit.application as application
from pygit.repo import Repository
from pygit.rev_parse_previous_cli import run_rev_parse_previous


def _seed_repo(path: Path) -> tuple[Repository, str, str]:
    repo = Repository.init(str(path))
    (repo.worktree / "f.txt").write_text("base\n", encoding="utf-8")
    repo.add(["f.txt"])
    base = repo.commit("base", author_name="Phase416", author_email="phase416@example.invalid")
    repo.refs.set_branch("topic", base, message="branch: topic")
    repo.checkout("topic")
    (repo.worktree / "f.txt").write_text("topic\n", encoding="utf-8")
    repo.add(["f.txt"])
    topic = repo.commit("topic", author_name="Phase416", author_email="phase416@example.invalid")
    repo.checkout("main")
    return repo, base, topic


def test_rev_parse_previous_outputs_genuine_sha256_oid(tmp_path: Path, monkeypatch, capsys) -> None:
    repo, _, topic = _seed_repo(tmp_path / "repo")
    capsys.readouterr()
    monkeypatch.chdir(repo.worktree)

    assert run_rev_parse_previous(["@{-1}"]) == 0
    assert capsys.readouterr().out == topic + "\n"
    assert len(topic) == 64


def test_rev_parse_previous_ref_output_modes(tmp_path: Path, monkeypatch, capsys) -> None:
    repo, _, _ = _seed_repo(tmp_path / "repo")
    capsys.readouterr()
    monkeypatch.chdir(repo.worktree)

    assert run_rev_parse_previous(["--abbrev-ref", "@{-1}"]) == 0
    assert capsys.readouterr().out == "topic\n"

    assert run_rev_parse_previous(["--symbolic-full-name", "@{-1}"]) == 0
    assert capsys.readouterr().out == "refs/heads/topic\n"


def test_rev_parse_previous_detached_ref_modes_emit_nothing(tmp_path: Path, monkeypatch, capsys) -> None:
    repo, base, _ = _seed_repo(tmp_path / "repo")
    repo.checkout("topic")
    repo.refs.set_head_detached(base, message="checkout: moving from topic to " + base)
    repo.checkout("main")
    capsys.readouterr()
    monkeypatch.chdir(repo.worktree)

    assert run_rev_parse_previous(["--abbrev-ref", "@{-1}"]) == 0
    assert capsys.readouterr().out == ""
    assert run_rev_parse_previous(["--symbolic-full-name", "@{-1}"]) == 0
    assert capsys.readouterr().out == ""


def test_rev_parse_previous_verify_quiet_missing_history(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    capsys.readouterr()
    monkeypatch.chdir(repo.worktree)

    assert run_rev_parse_previous(["--verify", "--quiet", "@{-1}"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_application_routes_only_supported_previous_rev_parse_shapes(monkeypatch) -> None:
    calls: list[tuple[str, list[str]]] = []

    def focused(argv):
        calls.append(("focused", list(argv)))
        return 0

    def legacy():
        calls.append(("legacy", []))

    monkeypatch.setattr(application, "run_rev_parse_previous", focused)
    monkeypatch.setattr(application, "launcher_main", legacy)

    for argv in (
        ["pygit", "rev-parse", "@{-1}"],
        ["pygit", "rev-parse", "--verify", "@{-2}"],
        ["pygit", "rev-parse", "--verify", "--quiet", "@{-1}"],
        ["pygit", "rev-parse", "--abbrev-ref", "@{-1}"],
        ["pygit", "rev-parse", "--symbolic-full-name", "@{-1}"],
    ):
        calls.clear()
        monkeypatch.setattr(sys, "argv", argv)
        application.main()
        assert calls == [("focused", argv[2:])]

    calls.clear()
    monkeypatch.setattr(sys, "argv", ["pygit", "rev-parse", "HEAD"])
    application.main()
    assert calls == [("legacy", [])]

    calls.clear()
    monkeypatch.setattr(sys, "argv", ["pygit", "rev-parse", "--short", "@{-1}"])
    application.main()
    assert calls == [("legacy", [])]


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    git = shutil.which("git")
    if git is None:
        pytest.skip("native git is unavailable")
    return subprocess.run([git, *args], cwd=str(cwd) if cwd else None, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def test_native_git_and_pygit_rev_parse_previous_match(tmp_path: Path, monkeypatch, capsys) -> None:
    native = tmp_path / "native"
    init = _git("init", "-q", "-b", "main", "--object-format=sha256", str(native))
    if init.returncode != 0:
        pytest.skip("native git does not support SHA-256 repositories")
    assert _git("config", "user.name", "Phase416", cwd=native).returncode == 0
    assert _git("config", "user.email", "phase416@example.invalid", cwd=native).returncode == 0
    (native / "f.txt").write_text("base\n", encoding="utf-8")
    assert _git("add", "f.txt", cwd=native).returncode == 0
    assert _git("commit", "-q", "-m", "base", cwd=native).returncode == 0
    assert _git("checkout", "-q", "-b", "topic", cwd=native).returncode == 0
    (native / "f.txt").write_text("topic\n", encoding="utf-8")
    assert _git("commit", "-qam", "topic", cwd=native).returncode == 0
    assert _git("checkout", "-q", "main", cwd=native).returncode == 0

    native_raw = _git("rev-parse", "@{-1}", cwd=native)
    native_short = _git("rev-parse", "--abbrev-ref", "@{-1}", cwd=native)
    native_full = _git("rev-parse", "--symbolic-full-name", "@{-1}", cwd=native)
    assert native_raw.returncode == native_short.returncode == native_full.returncode == 0
    assert len(native_raw.stdout.strip()) == 64
    assert native_short.stdout == "topic\n"
    assert native_full.stdout == "refs/heads/topic\n"

    repo, _, topic = _seed_repo(tmp_path / "pygit")
    capsys.readouterr()
    monkeypatch.chdir(repo.worktree)
    assert run_rev_parse_previous(["@{-1}"]) == 0
    pygit_raw = capsys.readouterr().out
    assert run_rev_parse_previous(["--abbrev-ref", "@{-1}"]) == 0
    pygit_short = capsys.readouterr().out
    assert run_rev_parse_previous(["--symbolic-full-name", "@{-1}"]) == 0
    pygit_full = capsys.readouterr().out

    assert pygit_raw == topic + "\n"
    assert len(topic) == 64
    assert pygit_short == native_short.stdout
    assert pygit_full == native_full.stdout
