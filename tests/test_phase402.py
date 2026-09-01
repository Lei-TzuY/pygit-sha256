"""Phase402: complete native check-ref-format CLI option parity."""

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


def test_no_allow_onelevel_restores_default_rejection() -> None:
    assert dispatch(["check-ref-format", "--allow-onelevel", "topic"]) == 0
    assert dispatch(
        ["check-ref-format", "--allow-onelevel", "--no-allow-onelevel", "topic"]
    ) == 1


def test_allow_onelevel_last_option_wins() -> None:
    assert dispatch(
        ["check-ref-format", "--no-allow-onelevel", "--allow-onelevel", "topic"]
    ) == 0


def test_print_is_deprecated_normalize_alias(capsys) -> None:
    assert dispatch(["check-ref-format", "--print", "//refs//heads//topic"]) == 0
    assert capsys.readouterr().out == "refs/heads/topic\n"


def test_print_composes_with_refspec_pattern(capsys) -> None:
    assert dispatch(
        ["check-ref-format", "--print", "--refspec-pattern", "//refs//heads//*"]
    ) == 0
    assert capsys.readouterr().out == "refs/heads/*\n"


def test_branch_mode_prints_checked_name(capsys) -> None:
    assert dispatch(["check-ref-format", "--branch", "topic"]) == 0
    assert capsys.readouterr().out == "topic\n"


@pytest.mark.parametrize(
    "extra",
    [
        ["--allow-onelevel"],
        ["--no-allow-onelevel"],
        ["--normalize"],
        ["--print"],
        ["--refspec-pattern"],
    ],
)
def test_branch_mode_rejects_other_modes(extra: list[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        dispatch(["check-ref-format", "--branch", *extra, "topic"])
    assert excinfo.value.code == 2


@pytest.mark.parametrize(
    "argv",
    [
        ["--allow-onelevel", "--no-allow-onelevel", "topic"],
        ["--no-allow-onelevel", "--allow-onelevel", "topic"],
        ["--print", "//refs//heads//topic"],
        ["--print", "--refspec-pattern", "//refs//heads//*"],
        ["--branch", "topic"],
    ],
)
def test_cli_matches_native_git(argv: list[str], capsys) -> None:
    native = _native(argv)
    code = dispatch(["check-ref-format", *argv])
    captured = capsys.readouterr()
    assert code == native.returncode
    assert captured.out == native.stdout


@pytest.mark.parametrize(
    "extra",
    [
        ["--allow-onelevel"],
        ["--no-allow-onelevel"],
        ["--normalize"],
        ["--print"],
        ["--refspec-pattern"],
    ],
)
def test_branch_incompatible_options_match_native_failure(extra: list[str]) -> None:
    native = _native(["--branch", *extra, "topic"])
    assert native.returncode != 0
    with pytest.raises(SystemExit):
        dispatch(["check-ref-format", "--branch", *extra, "topic"])
