from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from pygit.init_cli import run_init
from pygit.objects import BlobObject
from pygit.repo import Repository


def test_git_default_storage_environment_is_honored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "repo"
    monkeypatch.setenv("GIT_DEFAULT_HASH", "sha256")
    monkeypatch.setenv("GIT_DEFAULT_REF_FORMAT", "files")

    assert run_init(["-q", str(target)]) == 0

    repo = Repository(str(target))
    oid = repo.store.write(BlobObject(b"phase385\n"))
    assert len(oid) == 64
    int(oid, 16)


@pytest.mark.parametrize(
    ("env_name", "env_value", "message"),
    [
        ("GIT_DEFAULT_HASH", "sha1", "unsupported object format"),
        ("GIT_DEFAULT_HASH", "SHA256", "unsupported object format"),
        ("GIT_DEFAULT_HASH", "", "unsupported object format"),
        ("GIT_DEFAULT_REF_FORMAT", "reftable", "unsupported ref format"),
        ("GIT_DEFAULT_REF_FORMAT", "", "unsupported ref format"),
    ],
)
def test_unsupported_environment_default_fails_before_filesystem_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
    env_value: str,
    message: str,
) -> None:
    target = tmp_path / "repo"
    monkeypatch.setenv(env_name, env_value)

    with pytest.raises(ValueError, match=message):
        run_init([str(target)])

    assert not target.exists()


def test_cli_object_format_overrides_environment_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "repo"
    monkeypatch.setenv("GIT_DEFAULT_HASH", "sha1")

    assert run_init(["-q", "--object-format=sha256", str(target)]) == 0

    repo = Repository(str(target))
    assert len(repo.store.write(BlobObject(b"cli-wins\n"))) == 64


def test_cli_ref_format_overrides_environment_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "repo"
    monkeypatch.setenv("GIT_DEFAULT_REF_FORMAT", "reftable")

    assert run_init(["-q", "--ref-format=files", str(target)]) == 0

    assert (target / ".pygit" / "refs").is_dir()


def test_explicit_format_only_overrides_its_corresponding_environment_variable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "repo"
    monkeypatch.setenv("GIT_DEFAULT_HASH", "sha1")
    monkeypatch.setenv("GIT_DEFAULT_REF_FORMAT", "reftable")

    with pytest.raises(ValueError, match="unsupported ref format"):
        run_init(["--object-format=sha256", str(target)])

    assert not target.exists()


def test_native_git_environment_and_cli_precedence_parity(tmp_path: Path) -> None:
    git = shutil.which("git")
    if git is None:
        pytest.skip("native Git is not available")

    native_env_defaults = tmp_path / "native-env"
    env = os.environ.copy()
    env["GIT_DEFAULT_HASH"] = "sha256"
    env["GIT_DEFAULT_REF_FORMAT"] = "files"
    subprocess.run(
        [git, "init", "-q", str(native_env_defaults)],
        env=env,
        check=True,
    )
    assert subprocess.run(
        [git, "-C", str(native_env_defaults), "rev-parse", "--show-object-format"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip() == "sha256"
    assert subprocess.run(
        [git, "-C", str(native_env_defaults), "rev-parse", "--show-ref-format"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip() == "files"

    native_cli_wins = tmp_path / "native-cli"
    override_env = os.environ.copy()
    override_env["GIT_DEFAULT_HASH"] = "sha1"
    override_env["GIT_DEFAULT_REF_FORMAT"] = "reftable"
    subprocess.run(
        [
            git,
            "init",
            "-q",
            "--object-format=sha256",
            "--ref-format=files",
            str(native_cli_wins),
        ],
        env=override_env,
        check=True,
    )
    assert subprocess.run(
        [git, "-C", str(native_cli_wins), "rev-parse", "--show-object-format"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip() == "sha256"
    assert subprocess.run(
        [git, "-C", str(native_cli_wins), "rev-parse", "--show-ref-format"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip() == "files"


def test_pygit_cli_precedence_matches_native_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "ours"
    monkeypatch.setenv("GIT_DEFAULT_HASH", "sha1")
    monkeypatch.setenv("GIT_DEFAULT_REF_FORMAT", "reftable")

    assert run_init(
        [
            "-q",
            "--object-format=sha256",
            "--ref-format=files",
            str(target),
        ]
    ) == 0

    repo = Repository(str(target))
    assert len(repo.store.write(BlobObject(b"precedence\n"))) == 64
    assert (target / ".pygit" / "refs").is_dir()
