"""Phase404 regression tests for ordinary check-ref-format normalization."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from pygit.entrypoint import dispatch
from pygit.ref_query import check_ref_format


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


@pytest.mark.parametrize("refname", [
    "refs/heads/topic/",
    "refs//heads//topic/",
    "//refs//heads//topic/",
    "refs/heads//",
    "/",
    "///",
])
def test_normalize_rejects_trailing_slash(refname: str) -> None:
    with pytest.raises(ValueError):
        check_ref_format(refname, normalize=True)
    assert dispatch(["check-ref-format", "--normalize", refname]) == 1


def test_normalize_still_collapses_valid_slashes(capsys) -> None:
    assert dispatch(["check-ref-format", "--normalize", "//refs//heads//topic"]) == 0
    assert capsys.readouterr().out == "refs/heads/topic\n"


def test_print_alias_rejects_trailing_slash() -> None:
    assert dispatch(["check-ref-format", "--print", "refs/heads/topic/"]) == 1


@pytest.mark.parametrize("refname", [
    "refs/heads/topic/",
    "refs//heads//topic/",
    "//refs//heads//topic/",
    "refs/heads//",
    "/",
    "///",
    "//refs//heads//topic",
])
def test_normalize_matches_native_git(refname: str, capsys) -> None:
    argv = ["--normalize", refname]
    native = _native(argv)
    code = dispatch(["check-ref-format", *argv])
    captured = capsys.readouterr()
    assert code == native.returncode
    assert captured.out == native.stdout
