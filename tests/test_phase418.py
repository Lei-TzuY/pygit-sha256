"""Phase418 regressions for ``branch -M @{-N} <new>``."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import pygit.application as application
from pygit.branch_move_previous_cli import run_branch_move_previous
from pygit.repo import Repository


def _seed_repo(path: Path) -> tuple[Repository, str, str]:
    repo = Repository.init(str(path))
    (repo.worktree / "f.txt").write_text("base\n", encoding="utf-8")
    repo.add(["f.txt"])
    base = repo.commit("base", author_name="Phase418", author_email="p418@example.invalid")
    repo.refs.set_branch("topic", base, message="branch: topic")
    repo.refs.set_branch("taken", base, message="branch: taken")
    repo.checkout("topic")
    (repo.worktree / "f.txt").write_text("topic\n", encoding="utf-8")
    repo.add(["f.txt"])
    topic = repo.commit("topic", author_name="Phase418", author_email="p418@example.invalid")
    repo.checkout("main")
    return repo, base, topic


def test_force_move_replaces_destination_ref_and_reflog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, base, topic = _seed_repo(tmp_path / "repo")
    repo.refs.set_branch("taken", topic, message="taken move")
    repo.refs.set_branch("taken", base, message="taken back")
    monkeypatch.chdir(repo.worktree)

    assert run_branch_move_previous(["-M", "@{-1}", "taken"]) == 0

    reopened = Repository(str(repo.worktree))
    assert reopened.refs.get_branch("topic") is None
    assert reopened.refs.get_branch("taken") == topic
    assert reopened.refs.current_branch() == "main"
    entries = reopened.refs.read_reflog("refs/heads/taken")
    assert entries[0].message == "Branch: renamed refs/heads/topic to refs/heads/taken"
    assert any(entry.message == "commit: topic" for entry in entries[1:])
    assert all(entry.message not in {"taken move", "taken back"} for entry in entries)


def test_force_move_rejects_checked_out_destination_before_mutation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, base, topic = _seed_repo(tmp_path / "repo")
    monkeypatch.chdir(repo.worktree)

    with pytest.raises(ValueError, match="checked-out branch 'main'"):
        run_branch_move_previous(["-M", "@{-1}", "main"])

    reopened = Repository(str(repo.worktree))
    assert reopened.refs.get_branch("topic") == topic
    assert reopened.refs.get_branch("main") == base
    assert reopened.refs.current_branch() == "main"


def test_force_move_current_source_to_noncurrent_destination_updates_head(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, base, topic = _seed_repo(tmp_path / "repo")
    repo.checkout("topic")
    monkeypatch.chdir(repo.worktree)
    # main -> topic is @{-1}; topic itself is @{-2} in the longer seed history.
    assert run_branch_move_previous(["-M", "@{-2}", "taken"]) == 0
    reopened = Repository(str(repo.worktree))
    assert reopened.refs.current_branch() == "taken"
    assert reopened.refs.get_branch("taken") == topic
    assert reopened.refs.get_branch("topic") is None
    assert reopened.refs.get_head() == "ref: refs/heads/taken"


def test_force_move_long_option_orderings_route(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(application, "run_branch_move_previous", lambda argv: calls.append(list(argv)) or 0)
    monkeypatch.setattr(application, "launcher_main", lambda: calls.append(["legacy"]))

    for flags in (("--move", "--force"), ("--force", "--move"), ("-m", "-f"), ("-f", "-m")):
        calls.clear()
        monkeypatch.setattr(sys, "argv", ["pygit", "branch", *flags, "@{-1}", "taken"])
        application.main()
        assert calls == [[*flags, "@{-1}", "taken"]]


def test_native_sha256_git_force_move_previous_selector(tmp_path: Path) -> None:
    git = shutil.which("git")
    if git is None:
        pytest.skip("native git required")
    repo = tmp_path / "native"
    subprocess.run([git, "init", "--object-format=sha256", "-q", str(repo)], check=True)
    subprocess.run([git, "-C", str(repo), "config", "user.name", "Phase418"], check=True)
    subprocess.run([git, "-C", str(repo), "config", "user.email", "p418@example.invalid"], check=True)
    (repo / "f").write_text("base\n", encoding="utf-8")
    subprocess.run([git, "-C", str(repo), "add", "f"], check=True)
    subprocess.run([git, "-C", str(repo), "commit", "-qm", "base"], check=True)
    subprocess.run([git, "-C", str(repo), "branch", "-m", "main"], check=True)
    subprocess.run([git, "-C", str(repo), "branch", "topic"], check=True)
    subprocess.run([git, "-C", str(repo), "branch", "taken"], check=True)
    subprocess.run([git, "-C", str(repo), "checkout", "-q", "topic"], check=True)
    (repo / "f").write_text("topic\n", encoding="utf-8")
    subprocess.run([git, "-C", str(repo), "add", "f"], check=True)
    subprocess.run([git, "-C", str(repo), "commit", "-qm", "topic"], check=True)
    topic = subprocess.check_output([git, "-C", str(repo), "rev-parse", "topic"], text=True).strip()
    subprocess.run([git, "-C", str(repo), "checkout", "-q", "main"], check=True)
    result = subprocess.run([git, "-C", str(repo), "branch", "-M", "@{-1}", "taken"], check=True, text=True, stdout=subprocess.PIPE)
    assert result.stdout == ""
    assert subprocess.check_output([git, "-C", str(repo), "rev-parse", "taken"], text=True).strip() == topic
    assert subprocess.check_output([git, "-C", str(repo), "branch", "--show-current"], text=True).strip() == "main"
    assert subprocess.check_output([git, "-C", str(repo), "reflog", "show", "taken", "-1", "--format=%gs"], text=True).strip() == "Branch: renamed refs/heads/topic to refs/heads/taken"
