"""Phase403: keep refspec-pattern normalization inside Git's slash boundary."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from pygit.entrypoint import dispatch


def _native(argv: list[str]) -> subprocess.CompletedProcess[str]:
    git = shutil.which("git")
    if git is None:
        pytest.skip("native git is unavailable")
    return subprocess.run(
        [git, "check-ref-format", *argv],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


@pytest.mark.parametrize(
    "refname",
    [
        "refs/heads/*/",
        "refs//heads//*/",
        "//refs//heads//*/",
        "refs/heads//",
        "/",
        "///",
    ],
)
def test_refspec_normalize_does_not_erase_invalid_trailing_slash(refname: str) -> None:
    assert dispatch(
        ["check-ref-format", "--normalize", "--refspec-pattern", refname]
    ) == 1


def test_refspec_normalize_still_collapses_leading_and_internal_slashes(capsys) -> None:
    assert dispatch(
        [
            "check-ref-format",
            "--normalize",
            "--refspec-pattern",
            "//refs//heads//*",
        ]
    ) == 0
    assert capsys.readouterr().out == "refs/heads/*\n"


def test_print_alias_obeys_same_trailing_slash_boundary() -> None:
    assert dispatch(
        ["check-ref-format", "--print", "--refspec-pattern", "refs/heads/*/"]
    ) == 1


@pytest.mark.parametrize(
    "refname",
    [
        "refs/heads/*/",
        "refs//heads//*/",
        "//refs//heads//*/",
        "refs/heads//",
        "/",
        "///",
        "//refs//heads//*",
    ],
)
def test_refspec_normalize_matches_native_git(refname: str, capsys) -> None:
    argv = ["--normalize", "--refspec-pattern", refname]
    native = _native(argv)
    code = dispatch(["check-ref-format", *argv])
    captured = capsys.readouterr()
    assert code == native.returncode
    assert captured.out == native.stdout


def test_non_normalized_refspec_trailing_slash_remains_rejected() -> None:
    assert dispatch(
        ["check-ref-format", "--refspec-pattern", "refs/heads/*/"]
    ) == 1
