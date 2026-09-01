"""Phase401: Git-compatible check-ref-format --refspec-pattern."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from pygit.entrypoint import dispatch
from pygit.refspec_format import check_refspec_pattern


@pytest.mark.parametrize(
    "pattern",
    [
        "refs/heads/*",
        "foo/bar*/baz",
        "foo/*/bar",
        "refs/remotes/origin/topic",
    ],
)
def test_refspec_pattern_accepts_native_valid_shapes(pattern: str) -> None:
    assert check_refspec_pattern(pattern) == pattern


def test_refspec_pattern_rejects_multiple_wildcards() -> None:
    with pytest.raises(ValueError, match="at most one"):
        check_refspec_pattern("foo/bar*/baz*")


@pytest.mark.parametrize(
    "pattern",
    [
        "refs/heads/bad..*",
        "refs/heads/.hidden*",
        "refs/heads/topic.lock/*",
        "refs/heads/has space*",
        "refs/heads/a@{b*",
        "refs/heads/trailing.*.",
        "refs//heads/*",
    ],
)
def test_refspec_pattern_keeps_ordinary_ref_safety_rules(pattern: str) -> None:
    with pytest.raises(ValueError):
        check_refspec_pattern(pattern)


def test_refspec_pattern_onelevel_requires_explicit_opt_in() -> None:
    with pytest.raises(ValueError):
        check_refspec_pattern("topic*")
    assert check_refspec_pattern("topic*", allow_onelevel=True) == "topic*"


def test_refspec_pattern_normalize_returns_normalized_wildcard(capsys) -> None:
    assert dispatch(
        [
            "check-ref-format",
            "--refspec-pattern",
            "--normalize",
            "//refs//heads//*",
        ]
    ) == 0
    assert capsys.readouterr().out.strip() == "refs/heads/*"


def test_refspec_pattern_cli_rejects_multiple_wildcards(capsys) -> None:
    assert dispatch(
        ["check-ref-format", "--refspec-pattern", "refs/heads/*/topic*"]
    ) == 1
    assert "at most one" in capsys.readouterr().err


def test_refspec_pattern_does_not_change_ordinary_star_rejection(capsys) -> None:
    assert dispatch(["check-ref-format", "refs/heads/*"]) == 1
    assert "forbidden character" in capsys.readouterr().err


def test_refspec_pattern_matches_native_git() -> None:
    git = shutil.which("git")
    if git is None:
        pytest.skip("native git is unavailable")

    cases = [
        (["--refspec-pattern", "refs/heads/*"], 0),
        (["--refspec-pattern", "foo/bar*/baz"], 0),
        (["--refspec-pattern", "foo/*/bar"], 0),
        (["--refspec-pattern", "foo/bar*/baz*"], 1),
        (["--refspec-pattern", "foo/bar*baz/"], 1),
        (["--refspec-pattern", "--allow-onelevel", "foo*"], 0),
    ]

    for argv, expected in cases:
        native = subprocess.run(
            [git, "check-ref-format", *argv],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert native.returncode == expected, (argv, native.stderr)
        assert dispatch(["check-ref-format", *argv]) == expected


def test_native_and_pygit_normalize_same_refspec_pattern(capsys) -> None:
    git = shutil.which("git")
    if git is None:
        pytest.skip("native git is unavailable")

    argv = ["--refspec-pattern", "--normalize", "//refs//heads//*"]
    native = subprocess.run(
        [git, "check-ref-format", *argv],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert dispatch(["check-ref-format", *argv]) == 0
    assert capsys.readouterr().out == native.stdout
