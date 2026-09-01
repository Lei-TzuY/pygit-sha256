from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from pygit.init_cli import run_init
from pygit.repo import Repository


def _head(path: Path) -> str:
    return (path / ".pygit" / "HEAD").read_text(encoding="utf-8").strip()


def test_init_short_initial_branch_creates_unborn_symbolic_head(tmp_path: Path) -> None:
    target = tmp_path / "repo"

    assert run_init(["-b", "topic", str(target)]) == 0

    assert _head(target) == "ref: refs/heads/topic"
    assert not (target / ".pygit" / "refs" / "heads" / "topic").exists()
    assert not (target / ".pygit" / "logs" / "HEAD").exists()
    repo = Repository(str(target))
    assert repo.refs.current_branch() == "topic"
    assert repo.refs.resolve_head() is None


def test_init_long_initial_branch_accepts_nested_and_dash_names(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    dashed = tmp_path / "dashed"

    assert run_init(["--initial-branch", "feature/api/v2", str(nested)]) == 0
    assert _head(nested) == "ref: refs/heads/feature/api/v2"

    # Native git init accepts a leading '-' here because the stored full ref is
    # refs/heads/-topic; this intentionally differs from check-ref-format --branch.
    assert run_init(["--initial-branch=-topic", str(dashed)]) == 0
    assert _head(dashed) == "ref: refs/heads/-topic"


def test_invalid_initial_branch_is_rejected_before_filesystem_mutation(tmp_path: Path) -> None:
    target = tmp_path / "repo"

    with pytest.raises(ValueError, match="invalid initial branch name"):
        run_init(["-b", "bad..name", str(target)])

    assert not target.exists()


def test_reinit_ignores_initial_branch_and_preserves_head(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / "repo"
    assert run_init(["-b", "alpha", str(target)]) == 0
    capsys.readouterr()

    assert run_init(["-b", "beta", str(target)]) == 0
    captured = capsys.readouterr()

    assert _head(target) == "ref: refs/heads/alpha"
    assert "warning: re-init: ignored --initial-branch=beta" in captured.err


def test_default_init_behavior_remains_main(tmp_path: Path) -> None:
    target = tmp_path / "repo"

    assert run_init([str(target)]) == 0

    assert _head(target) == "ref: refs/heads/main"


def test_native_git_initial_branch_and_reinit_parity(tmp_path: Path) -> None:
    git = shutil.which("git")
    if git is None:
        pytest.skip("native Git is not available")

    native = tmp_path / "native"
    ours = tmp_path / "ours"

    subprocess.run([git, "init", "-q", "-b", "feature/api", str(native)], check=True)
    assert run_init(["-b", "feature/api", str(ours)]) == 0

    native_head = (native / ".git" / "HEAD").read_text(encoding="utf-8").strip()
    assert native_head == _head(ours) == "ref: refs/heads/feature/api"

    native_reinit = subprocess.run(
        [git, "-C", str(native), "init", "-q", "-b", "ignored"],
        check=True,
        text=True,
        capture_output=True,
    )
    assert "ignored --initial-branch=ignored" in native_reinit.stderr
    assert (native / ".git" / "HEAD").read_text(encoding="utf-8").strip() == native_head


def test_native_git_rejects_same_invalid_initial_branch_shape(tmp_path: Path) -> None:
    git = shutil.which("git")
    if git is None:
        pytest.skip("native Git is not available")

    native = tmp_path / "native"
    ours = tmp_path / "ours"

    native_result = subprocess.run(
        [git, "init", "-q", "-b", "bad..name", str(native)],
        text=True,
        capture_output=True,
    )
    assert native_result.returncode != 0

    with pytest.raises(ValueError, match="invalid initial branch name"):
        run_init(["-b", "bad..name", str(ours)])
    assert not ours.exists()
