"""Phase405 regressions for check-ref-format --branch @{-N}."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from pygit.branch_checkout import expand_previous_checkout
from pygit.entrypoint import dispatch
from pygit.repo import Repository


def _seed_checkout_history(path: Path) -> Repository:
    repo = Repository.init(str(path))
    main_oid = "1" * 64
    topic_oid = "2" * 64
    repo.refs.set_branch("main", main_oid, message="seed main")
    repo.refs.set_branch("topic", topic_oid, message="seed topic")
    repo.refs.set_head_symbolic("topic", message="checkout: moving to topic")
    repo.refs.set_head_symbolic("main", message="checkout: moving to main")
    return repo


def test_branch_previous_checkout_expands_legacy_pygit_history(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _seed_checkout_history(tmp_path)
    # Repository.init() intentionally reports initialization on stdout.  Clear
    # that fixture output before asserting the command's Git-compatible output.
    capsys.readouterr()
    monkeypatch.chdir(tmp_path)

    assert dispatch(["check-ref-format", "--branch", "@{-1}"]) == 0
    assert capsys.readouterr().out == "topic\n"

    assert dispatch(["check-ref-format", "--branch", "@{-2}"]) == 0
    assert capsys.readouterr().out == "main\n"


def test_previous_checkout_supports_leading_zero_index(tmp_path: Path) -> None:
    repo = _seed_checkout_history(tmp_path)
    assert expand_previous_checkout(repo, "@{-01}") == "topic"


def test_previous_checkout_rejects_zero_and_missing_history(
    tmp_path: Path, monkeypatch
) -> None:
    _seed_checkout_history(tmp_path)
    monkeypatch.chdir(tmp_path)

    assert dispatch(["check-ref-format", "--branch", "@{-0}"]) == 1
    assert dispatch(["check-ref-format", "--branch", "@{-3}"]) == 1


def test_previous_checkout_not_used_outside_branch_mode(tmp_path: Path, monkeypatch) -> None:
    _seed_checkout_history(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert dispatch(["check-ref-format", "@{-1}"]) == 1


def test_native_style_reflog_source_is_authoritative(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path))
    repo.refs._append_reflog(  # focused compatibility fixture
        "HEAD",
        "a" * 64,
        "b" * 64,
        "checkout: moving from feature/api to main",
        force=True,
    )
    assert expand_previous_checkout(repo, "@{-1}") == "feature/api"


def _native_previous_checkout(repo_path: Path, selector: str) -> subprocess.CompletedProcess[str]:
    git = shutil.which("git")
    if git is None:
        pytest.skip("native git is unavailable")
    return subprocess.run(
        [git, "-C", str(repo_path), "check-ref-format", "--branch", selector],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_previous_checkout_selector_shape_matches_native_git(tmp_path: Path) -> None:
    git = shutil.which("git")
    if git is None:
        pytest.skip("native git is unavailable")

    native = tmp_path / "native"
    subprocess.run([git, "init", "-q", "-b", "main", str(native)], check=True)
    subprocess.run([git, "-C", str(native), "config", "user.name", "Phase405"], check=True)
    subprocess.run([git, "-C", str(native), "config", "user.email", "phase405@example.invalid"], check=True)
    (native / "file.txt").write_text("one\n", encoding="utf-8")
    subprocess.run([git, "-C", str(native), "add", "file.txt"], check=True)
    subprocess.run([git, "-C", str(native), "commit", "-q", "-m", "one"], check=True)
    subprocess.run([git, "-C", str(native), "switch", "-q", "-c", "topic"], check=True)
    subprocess.run([git, "-C", str(native), "switch", "-q", "main"], check=True)

    assert _native_previous_checkout(native, "@{-1}").stdout == "topic\n"
    assert _native_previous_checkout(native, "@{-2}").stdout == "main\n"
    assert _native_previous_checkout(native, "@{-0}").returncode != 0
    assert _native_previous_checkout(native, "@{-3}").returncode != 0
