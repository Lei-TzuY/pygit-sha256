"""Phase407 regressions for operation-level previous-checkout navigation."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from pygit.branch_checkout import checkout_previous
from pygit.repo import Repository


def _seed_repo(path: Path) -> tuple[Repository, str]:
    repo = Repository.init(str(path))
    (repo.worktree / "f.txt").write_text("base\n", encoding="utf-8")
    repo.add(["f.txt"])
    base = repo.commit(
        "base",
        author_name="Phase407",
        author_email="phase407@example.invalid",
    )
    repo.refs.set_branch("topic", base, message="branch: topic")
    return repo, base


def test_checkout_previous_returns_to_symbolic_branch(tmp_path: Path) -> None:
    repo, _ = _seed_repo(tmp_path / "repo")
    repo.checkout("topic")
    repo.checkout("main")

    expanded = checkout_previous(repo)

    assert expanded == "topic"
    assert repo.refs.current_branch() == "topic"
    assert repo.reflog("HEAD")[0].message == "checkout: moving from main to topic"


def test_checkout_previous_can_select_older_checkout(tmp_path: Path) -> None:
    repo, _ = _seed_repo(tmp_path / "repo")
    repo.checkout("topic")
    repo.checkout("main")
    repo.checkout("topic")

    expanded = checkout_previous(repo, "@{-2}")

    assert expanded == "topic"
    assert repo.refs.current_branch() == "topic"


def test_checkout_previous_preserves_detached_sha256_destination(tmp_path: Path) -> None:
    repo, base = _seed_repo(tmp_path / "repo")
    repo.checkout(base)
    repo.checkout("main")

    expanded = checkout_previous(repo)

    assert expanded == base
    assert len(expanded) == 64
    assert repo.refs.current_branch() is None
    assert repo.refs.resolve_head() == base
    assert repo.reflog("HEAD")[0].message == f"checkout: moving from main to {base}"


def test_checkout_previous_rejects_non_selector_without_mutation(tmp_path: Path) -> None:
    repo, base = _seed_repo(tmp_path / "repo")
    before_head = (repo.pygit_dir / "HEAD").read_text(encoding="utf-8")
    before_log = list(repo.reflog("HEAD"))

    with pytest.raises(ValueError, match="not a previous checkout selector"):
        checkout_previous(repo, "main")

    assert repo.refs.resolve_head() == base
    assert (repo.pygit_dir / "HEAD").read_text(encoding="utf-8") == before_head
    assert repo.reflog("HEAD") == before_log


def test_checkout_previous_rejects_unavailable_selector(tmp_path: Path) -> None:
    repo, _ = _seed_repo(tmp_path / "repo")

    with pytest.raises(ValueError, match="does not name an earlier checkout"):
        checkout_previous(repo, "@{-1}")


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


def test_native_git_previous_checkout_records_expanded_destination(tmp_path: Path) -> None:
    native = tmp_path / "native"
    init = _git("init", "-q", "-b", "main", "--object-format=sha256", str(native))
    if init.returncode != 0:
        pytest.skip("native git does not support SHA-256 repositories")
    assert _git("config", "user.name", "Phase407", cwd=native).returncode == 0
    assert _git("config", "user.email", "phase407@example.invalid", cwd=native).returncode == 0
    (native / "f.txt").write_text("base\n", encoding="utf-8")
    assert _git("add", "f.txt", cwd=native).returncode == 0
    assert _git("commit", "-q", "-m", "base", cwd=native).returncode == 0
    assert _git("checkout", "-q", "-b", "topic", cwd=native).returncode == 0
    assert _git("checkout", "-q", "main", cwd=native).returncode == 0
    assert _git("checkout", "-q", "@{-1}", cwd=native).returncode == 0

    native_branch = _git("symbolic-ref", "--short", "HEAD", cwd=native)
    native_message = _git("reflog", "-1", "--format=%gs", "HEAD", cwd=native)
    assert native_branch.stdout == "topic\n"
    assert native_message.stdout == "checkout: moving from main to topic\n"

    repo, _ = _seed_repo(tmp_path / "pygit")
    repo.checkout("topic")
    repo.checkout("main")
    assert checkout_previous(repo) == "topic"
    assert repo.refs.current_branch() + "\n" == native_branch.stdout
    assert repo.reflog("HEAD")[0].message + "\n" == native_message.stdout
