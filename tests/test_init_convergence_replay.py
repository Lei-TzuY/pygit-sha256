from __future__ import annotations

import sys
from pathlib import Path

import pytest

from pygit import application
from pygit.init_cli import run_init
from pygit.objects import BlobObject
from pygit.repo import Repository


def _head(path: Path) -> str:
    return (path / ".pygit" / "HEAD").read_text(encoding="utf-8").strip()


def test_initial_branch_creates_unborn_symbolic_head(tmp_path: Path) -> None:
    target = tmp_path / "repo"

    assert run_init(["-q", "-b", "feature/api/v2", str(target)]) == 0

    assert _head(target) == "ref: refs/heads/feature/api/v2"
    assert not (target / ".pygit" / "refs" / "heads" / "feature" / "api" / "v2").exists()
    assert not (target / ".pygit" / "logs" / "HEAD").exists()
    repo = Repository(str(target))
    assert repo.refs.current_branch() == "feature/api/v2"
    assert repo.refs.resolve_head() is None


def test_initial_branch_accepts_leading_dash_and_rejects_bad_ref_before_mutation(
    tmp_path: Path,
) -> None:
    dashed = tmp_path / "dashed"
    invalid = tmp_path / "invalid"

    assert run_init(["-q", "--initial-branch=-topic", str(dashed)]) == 0
    assert _head(dashed) == "ref: refs/heads/-topic"

    with pytest.raises(ValueError, match="invalid initial branch name"):
        run_init(["-b", "bad..name", str(invalid)])
    assert not invalid.exists()


def test_quiet_reinit_preserves_head_and_keeps_warning_visible(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / "repo"

    assert run_init(["-q", "-b", "alpha", str(target)]) == 0
    assert capsys.readouterr().out == ""

    assert run_init(["-q", "-b", "beta", str(target)]) == 0
    captured = capsys.readouterr()

    assert captured.out == ""
    assert "warning: re-init: ignored --initial-branch=beta" in captured.err
    assert _head(target) == "ref: refs/heads/alpha"


def test_explicit_supported_formats_keep_sha256_files_storage(tmp_path: Path) -> None:
    target = tmp_path / "repo"

    assert run_init(
        [
            "-q",
            "--object-format=sha256",
            "--ref-format=files",
            str(target),
        ]
    ) == 0

    repo = Repository(str(target))
    oid = repo.store.write(BlobObject(b"clean-replay\n"))
    assert len(oid) == 64
    int(oid, 16)
    assert (target / ".pygit" / "refs").is_dir()


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("--object-format=sha1",), "unsupported object format"),
        (("--object-format=SHA256",), "unsupported object format"),
        (("--ref-format=reftable",), "unsupported ref format"),
    ],
)
def test_unsupported_cli_storage_format_fails_before_mutation(
    tmp_path: Path,
    args: tuple[str, ...],
    message: str,
) -> None:
    target = tmp_path / "repo"

    with pytest.raises(ValueError, match=message):
        run_init([*args, str(target)])

    assert not target.exists()


def test_git_default_storage_environment_is_honored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "repo"
    monkeypatch.setenv("GIT_DEFAULT_HASH", "sha256")
    monkeypatch.setenv("GIT_DEFAULT_REF_FORMAT", "files")

    assert run_init(["-q", str(target)]) == 0

    repo = Repository(str(target))
    assert len(repo.store.write(BlobObject(b"env-defaults\n"))) == 64
    assert (target / ".pygit" / "refs").is_dir()


@pytest.mark.parametrize(
    ("env_name", "env_value", "message"),
    [
        ("GIT_DEFAULT_HASH", "sha1", "unsupported object format"),
        ("GIT_DEFAULT_HASH", "", "unsupported object format"),
        ("GIT_DEFAULT_REF_FORMAT", "reftable", "unsupported ref format"),
        ("GIT_DEFAULT_REF_FORMAT", "", "unsupported ref format"),
    ],
)
def test_unsupported_environment_default_fails_before_mutation(
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


def test_cli_storage_options_override_only_corresponding_environment_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    succeeds = tmp_path / "succeeds"
    fails = tmp_path / "fails"
    monkeypatch.setenv("GIT_DEFAULT_HASH", "sha1")
    monkeypatch.setenv("GIT_DEFAULT_REF_FORMAT", "files")

    assert run_init(["-q", "--object-format=sha256", str(succeeds)]) == 0

    monkeypatch.setenv("GIT_DEFAULT_REF_FORMAT", "reftable")
    with pytest.raises(ValueError, match="unsupported ref format"):
        run_init(["--object-format=sha256", str(fails)])
    assert not fails.exists()


def test_application_routes_init_to_modern_porcelain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / "repo"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "pygit",
            "init",
            "-q",
            "--object-format=sha256",
            "--ref-format=files",
            "-b",
            "topic",
            str(target),
        ],
    )

    application.main()

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert _head(target) == "ref: refs/heads/topic"
