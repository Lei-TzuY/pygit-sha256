"""Phase406 regressions for native-style checkout reflog messages."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from pygit.branch_checkout import expand_previous_checkout
from pygit.repo import Repository


def _seed_branches(path: Path) -> Repository:
    repo = Repository.init(str(path))
    repo.refs.set_branch("main", "1" * 64, message="seed main")
    repo.refs.set_branch("topic", "2" * 64, message="seed topic")
    return repo


def test_symbolic_checkout_records_native_moving_from_source(tmp_path: Path) -> None:
    repo = _seed_branches(tmp_path)

    repo.refs.set_head_symbolic("topic", message="checkout: moving to topic")

    entry = repo.reflog("HEAD")[0]
    assert entry.message == "checkout: moving from main to topic"
    assert entry.old_sha == "1" * 64
    assert entry.new_sha == "2" * 64


def test_detached_checkout_source_uses_full_local_sha256(tmp_path: Path) -> None:
    repo = _seed_branches(tmp_path)
    detached = "a" * 64
    repo.refs.set_head_detached(detached, message="fixture: detach")

    repo.refs.set_head_symbolic("main", message="checkout: moving to main")

    assert repo.reflog("HEAD")[0].message == (
        f"checkout: moving from {detached} to main"
    )


def test_detaching_from_branch_records_symbolic_source(tmp_path: Path) -> None:
    repo = _seed_branches(tmp_path)
    detached = "b" * 64

    repo.refs.set_head_detached(detached, message="checkout: moving to HEAD~1")

    assert repo.reflog("HEAD")[0].message == "checkout: moving from main to HEAD~1"


def test_unrelated_reflog_messages_are_not_rewritten(tmp_path: Path) -> None:
    repo = _seed_branches(tmp_path)

    repo.refs.set_head_symbolic("topic", message="rebase: checkout topic")

    assert repo.reflog("HEAD")[0].message == "rebase: checkout topic"


def test_phase405_previous_checkout_uses_new_writer_without_inference(tmp_path: Path) -> None:
    repo = _seed_branches(tmp_path)
    repo.refs.set_head_symbolic("topic", message="checkout: moving to topic")
    repo.refs.set_head_symbolic("main", message="checkout: moving to main")

    assert expand_previous_checkout(repo, "@{-1}") == "topic"
    assert expand_previous_checkout(repo, "@{-2}") == "main"
    messages = [entry.message for entry in repo.reflog("HEAD")[:2]]
    assert messages == [
        "checkout: moving from topic to main",
        "checkout: moving from main to topic",
    ]


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


def test_native_git_checkout_reflog_message_shape(tmp_path: Path) -> None:
    native = tmp_path / "native"
    init = _git("init", "-q", "-b", "main", "--object-format=sha256", str(native))
    if init.returncode != 0:
        pytest.skip("native git does not support SHA-256 repositories")
    assert _git("config", "user.name", "Phase406", cwd=native).returncode == 0
    assert _git("config", "user.email", "phase406@example.invalid", cwd=native).returncode == 0
    (native / "f.txt").write_text("one\n", encoding="utf-8")
    assert _git("add", "f.txt", cwd=native).returncode == 0
    assert _git("commit", "-q", "-m", "one", cwd=native).returncode == 0
    assert _git("checkout", "-q", "-b", "topic", cwd=native).returncode == 0
    assert _git("checkout", "-q", "main", cwd=native).returncode == 0

    native_message = _git("reflog", "-1", "--format=%gs", "HEAD", cwd=native)
    assert native_message.returncode == 0
    assert native_message.stdout == "checkout: moving from topic to main\n"

    pygit_repo = _seed_branches(tmp_path / "pygit")
    pygit_repo.refs.set_head_symbolic("topic", message="checkout: moving to topic")
    pygit_repo.refs.set_head_symbolic("main", message="checkout: moving to main")
    assert pygit_repo.reflog("HEAD")[0].message + "\n" == native_message.stdout
