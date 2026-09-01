"""Phase415 regressions for ``branch -m @{-N} <new>``."""

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
    base = repo.commit(
        "base",
        author_name="Phase415",
        author_email="phase415@example.invalid",
    )
    repo.refs.set_branch("topic", base, message="branch: topic")
    repo.checkout("topic")
    (repo.worktree / "f.txt").write_text("topic\n", encoding="utf-8")
    repo.add(["f.txt"])
    topic = repo.commit(
        "topic",
        author_name="Phase415",
        author_email="phase415@example.invalid",
    )
    repo.checkout("main")
    return repo, base, topic


def test_branch_move_previous_renames_noncurrent_branch_and_preserves_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo, base, topic = _seed_repo(tmp_path / "repo")
    repo.config_set("branch", "topic.remote", "origin")
    repo.config_set("branch", "topic.merge", "refs/heads/topic")
    capsys.readouterr()
    monkeypatch.chdir(repo.worktree)

    assert run_branch_move_previous(["-m", "@{-1}", "renamed"]) == 0

    reopened = Repository(str(repo.worktree))
    assert reopened.refs.get_branch("topic") is None
    assert reopened.refs.get_branch("renamed") == topic
    assert reopened.refs.current_branch() == "main"
    assert reopened.refs.resolve_head() == base
    assert (reopened.worktree / "f.txt").read_text(encoding="utf-8") == "base\n"
    assert reopened.config_get("branch", "renamed.remote") == "origin"
    assert reopened.config_get("branch", "renamed.merge") == "refs/heads/topic"
    assert reopened.config_get("branch", "topic.remote") is None
    assert reopened.config_get("branch", "topic.merge") is None
    assert capsys.readouterr().out == ""

    entries = reopened.refs.read_reflog("refs/heads/renamed")
    assert entries[0].old_sha == topic
    assert entries[0].new_sha == topic
    assert entries[0].message == "Branch: renamed refs/heads/topic to refs/heads/renamed"
    assert any(entry.message == "commit: topic" for entry in entries[1:])
    assert not (reopened.pygit_dir / "logs" / "refs" / "heads" / "topic").exists()


def test_branch_move_previous_can_rename_current_branch_via_older_selector(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, base, _ = _seed_repo(tmp_path / "repo")
    monkeypatch.chdir(repo.worktree)

    assert run_branch_move_previous(["--move", "@{-2}", "primary"]) == 0

    reopened = Repository(str(repo.worktree))
    assert reopened.refs.get_branch("main") is None
    assert reopened.refs.get_branch("primary") == base
    assert reopened.refs.current_branch() == "primary"
    assert reopened.refs.get_head() == "ref: refs/heads/primary"
    assert reopened.refs.resolve_head() == base
    assert reopened.refs.read_reflog("HEAD")[0].message == (
        "Branch: renamed refs/heads/main to refs/heads/primary"
    )
    assert reopened.refs.read_reflog("refs/heads/primary")[0].message == (
        "Branch: renamed refs/heads/main to refs/heads/primary"
    )


def test_branch_move_previous_rejects_existing_destination_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, base, topic = _seed_repo(tmp_path / "repo")
    repo.refs.set_branch("taken", base, message="branch: taken")
    monkeypatch.chdir(repo.worktree)

    with pytest.raises(ValueError, match="already exists"):
        run_branch_move_previous(["-m", "@{-1}", "taken"])

    reopened = Repository(str(repo.worktree))
    assert reopened.refs.get_branch("topic") == topic
    assert reopened.refs.get_branch("taken") == base
    assert reopened.refs.current_branch() == "main"


def test_branch_move_previous_rejects_detached_previous_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, base, topic = _seed_repo(tmp_path / "repo")
    repo.refs.set_head_detached(topic, message="checkout: moving to detached-topic")
    repo.refs.delete_branch("topic")
    repo.refs.set_head_symbolic("main", message="checkout: moving to main")
    monkeypatch.chdir(repo.worktree)

    with pytest.raises(ValueError, match="no branch named"):
        run_branch_move_previous(["-m", "@{-1}", "renamed"])

    reopened = Repository(str(repo.worktree))
    assert reopened.refs.get_branch("renamed") is None
    assert reopened.refs.get_branch("main") == base
    assert reopened.refs.current_branch() == "main"


def test_application_routes_only_move_previous_selector_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, list[str]]] = []

    def move(argv):
        calls.append(("move", list(argv)))
        return 0

    def legacy():
        calls.append(("legacy", []))

    monkeypatch.setattr(application, "run_branch_move_previous", move)
    monkeypatch.setattr(application, "launcher_main", legacy)

    monkeypatch.setattr(
        sys,
        "argv",
        ["pygit", "branch", "-m", "@{-1}", "renamed"],
    )
    application.main()
    assert calls == [("move", ["-m", "@{-1}", "renamed"])]

    calls.clear()
    monkeypatch.setattr(
        sys,
        "argv",
        ["pygit", "branch", "--move", "@{-2}", "primary"],
    )
    application.main()
    assert calls == [("move", ["--move", "@{-2}", "primary"])]

    calls.clear()
    monkeypatch.setattr(sys, "argv", ["pygit", "branch", "-m", "topic", "renamed"])
    application.main()
    assert calls == [("legacy", [])]


def test_native_sha256_git_branch_move_previous_selector(tmp_path: Path) -> None:
    git = shutil.which("git")
    if git is None:
        pytest.skip("native git is required for the Phase415 differential")

    repo = tmp_path / "native"
    subprocess.run([git, "init", "--object-format=sha256", "-q", str(repo)], check=True)
    subprocess.run([git, "-C", str(repo), "config", "user.name", "Phase415"], check=True)
    subprocess.run(
        [git, "-C", str(repo), "config", "user.email", "phase415@example.invalid"],
        check=True,
    )
    (repo / "f.txt").write_text("base\n", encoding="utf-8")
    subprocess.run([git, "-C", str(repo), "add", "f.txt"], check=True)
    subprocess.run([git, "-C", str(repo), "commit", "-qm", "base"], check=True)
    subprocess.run([git, "-C", str(repo), "branch", "-m", "main"], check=True)
    subprocess.run([git, "-C", str(repo), "branch", "topic"], check=True)
    subprocess.run([git, "-C", str(repo), "config", "branch.topic.remote", "origin"], check=True)
    subprocess.run(
        [git, "-C", str(repo), "config", "branch.topic.merge", "refs/heads/topic"],
        check=True,
    )
    subprocess.run([git, "-C", str(repo), "checkout", "-q", "topic"], check=True)
    (repo / "f.txt").write_text("topic\n", encoding="utf-8")
    subprocess.run([git, "-C", str(repo), "add", "f.txt"], check=True)
    subprocess.run([git, "-C", str(repo), "commit", "-qm", "topic"], check=True)
    topic = subprocess.check_output(
        [git, "-C", str(repo), "rev-parse", "topic"], text=True
    ).strip()
    subprocess.run([git, "-C", str(repo), "checkout", "-q", "main"], check=True)

    result = subprocess.run(
        [git, "-C", str(repo), "branch", "-m", "@{-1}", "renamed"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert result.stdout == ""
    assert subprocess.check_output(
        [git, "-C", str(repo), "rev-parse", "renamed"], text=True
    ).strip() == topic
    assert subprocess.check_output(
        [git, "-C", str(repo), "branch", "--show-current"], text=True
    ).strip() == "main"
    assert subprocess.check_output(
        [git, "-C", str(repo), "config", "--get", "branch.renamed.remote"], text=True
    ).strip() == "origin"
    assert subprocess.check_output(
        [git, "-C", str(repo), "config", "--get", "branch.renamed.merge"], text=True
    ).strip() == "refs/heads/topic"
    old_config = subprocess.run(
        [git, "-C", str(repo), "config", "--get", "branch.topic.remote"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert old_config.returncode != 0
    assert subprocess.check_output(
        [git, "-C", str(repo), "reflog", "show", "renamed", "-1", "--format=%gs"],
        text=True,
    ).strip() == "Branch: renamed refs/heads/topic to refs/heads/renamed"
