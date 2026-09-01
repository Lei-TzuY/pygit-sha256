from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from pygit.init_cli import run_init
from pygit.objects import BlobObject
from pygit.repo import Repository


def _head(path: Path) -> str:
    return (path / ".pygit" / "HEAD").read_text(encoding="utf-8").strip()


def test_quiet_init_suppresses_ordinary_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / "repo"

    assert run_init(["-q", str(target)]) == 0
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == ""
    assert _head(target) == "ref: refs/heads/main"


def test_quiet_reinit_keeps_initial_branch_warning_visible(
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


def test_explicit_supported_formats_preserve_sha256_native_storage(tmp_path: Path) -> None:
    target = tmp_path / "repo"

    assert run_init(
        [
            "-q",
            "--object-format=sha256",
            "--ref-format=files",
            "-b",
            "feature/storage",
            str(target),
        ]
    ) == 0

    repo = Repository(str(target))
    oid = repo.store.write(BlobObject(b"phase384\n"))

    assert len(oid) == 64
    int(oid, 16)
    assert _head(target) == "ref: refs/heads/feature/storage"
    assert not (target / ".pygit" / "refs" / "heads" / "feature" / "storage").exists()


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("--object-format=sha1",), "unsupported object format"),
        (("--object-format=SHA256",), "unsupported object format"),
        (("--object-format=sha512",), "unsupported object format"),
        (("--ref-format=reftable",), "unsupported ref format"),
        (("--ref-format=nope",), "unsupported ref format"),
    ],
)
def test_unsupported_storage_format_fails_before_filesystem_mutation(
    tmp_path: Path,
    args: tuple[str, ...],
    message: str,
) -> None:
    target = tmp_path / "repo"

    with pytest.raises(ValueError, match=message):
        run_init([*args, str(target)])

    assert not target.exists()


def test_supported_formats_are_safe_on_reinit(tmp_path: Path) -> None:
    target = tmp_path / "repo"

    assert run_init(["-q", "-b", "stable", str(target)]) == 0
    assert run_init(
        [
            "-q",
            "--object-format",
            "sha256",
            "--ref-format",
            "files",
            str(target),
        ]
    ) == 0

    assert _head(target) == "ref: refs/heads/stable"


def test_native_git_supported_storage_format_and_quiet_parity(tmp_path: Path) -> None:
    git = shutil.which("git")
    if git is None:
        pytest.skip("native Git is not available")

    native = tmp_path / "native"
    ours = tmp_path / "ours"

    native_result = subprocess.run(
        [
            git,
            "init",
            "-q",
            "--object-format=sha256",
            "--ref-format=files",
            "-b",
            "feature/storage",
            str(native),
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    assert run_init(
        [
            "-q",
            "--object-format=sha256",
            "--ref-format=files",
            "-b",
            "feature/storage",
            str(ours),
        ]
    ) == 0

    assert native_result.stdout == ""
    assert native_result.stderr == ""
    assert (native / ".git" / "HEAD").read_text(encoding="utf-8").strip() == _head(ours)

    native_object_format = subprocess.run(
        [git, "-C", str(native), "rev-parse", "--show-object-format"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    native_ref_format = subprocess.run(
        [git, "-C", str(native), "rev-parse", "--show-ref-format"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()

    assert native_object_format == "sha256"
    assert native_ref_format == "files"
