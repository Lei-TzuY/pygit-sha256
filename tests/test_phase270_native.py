from __future__ import annotations

import os
import re
import subprocess

import pytest


def _git(cwd, *args, env=None, text=True):
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        check=True,
        text=text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout


def _commit_env(timestamp: int):
    env = os.environ.copy()
    value = f"@{timestamp} +0000"
    env["GIT_AUTHOR_DATE"] = value
    env["GIT_COMMITTER_DATE"] = value
    return env


def _git_version():
    value = subprocess.run(
        ["git", "--version"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout
    match = re.search(r"(\d+)\.(\d+)", value)
    assert match is not None
    return int(match.group(1)), int(match.group(2))


def _native_repo(tmp_path):
    if _git_version() < (2, 55):
        pytest.skip("Phase270 byte-level probe requires Git >= 2.55")

    repo = tmp_path / "native"
    subprocess.run(
        ["git", "init", "--object-format=sha256", "-q", str(repo)],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")

    (repo / "small.bin").write_bytes(b"sss")
    _git(repo, "add", "small.bin")
    _git(repo, "commit", "-qm", "small", env=_commit_env(1))
    c1 = _git(repo, "rev-parse", "HEAD").strip()

    (repo / "large.bin").write_bytes(b"LLLLLLLL")
    _git(repo, "add", "large.bin")
    _git(repo, "commit", "-qm", "large", env=_commit_env(2))
    large = _git(repo, "rev-parse", "HEAD:large.bin").strip()
    return repo, c1, large


def _raw(repo, *args):
    return _git(repo, "rev-list", *args, text=False)


def test_native_sha256_nul_count_is_newline_diagnostic(tmp_path):
    repo, _c1, _large = _native_repo(tmp_path)

    assert _raw(repo, "--objects", "-z", "--count", "HEAD") == b"6\n"
    assert _raw(
        repo,
        "--objects",
        "-z",
        "--boundary",
        "--max-count=1",
        "--count",
        "HEAD",
    ) == b"6\n"
    assert _raw(
        repo,
        "--objects",
        "--in-commit-order",
        "-z",
        "--count",
        "HEAD",
    ) == b"6\n"


def test_native_sha256_nul_filtered_counts_are_newline_diagnostics(tmp_path):
    repo, _c1, _large = _native_repo(tmp_path)

    assert _raw(
        repo,
        "--objects",
        "-z",
        "--filter=blob:none",
        "--count",
        "HEAD",
    ) == b"4\n"
    assert _raw(
        repo,
        "--objects",
        "-z",
        "--filter=object:type=tree",
        "--filter-provided-objects",
        "--count",
        "HEAD",
    ) == b"2\n"


def test_native_sha256_nul_omissions_precede_newline_count(tmp_path):
    repo, _c1, large = _native_repo(tmp_path)

    blob_none = _raw(
        repo,
        "--objects",
        "-z",
        "--filter=blob:none",
        "--filter-print-omitted",
        "--count",
        "HEAD",
    )
    assert b"\0" not in blob_none
    blob_none_lines = blob_none.decode().splitlines()
    assert blob_none_lines[-1] == "4"
    assert len(blob_none_lines[:-1]) == 2
    assert all(line.startswith("~") for line in blob_none_lines[:-1])

    blob_limit = _raw(
        repo,
        "--objects",
        "--in-commit-order",
        "-z",
        "--filter=blob:limit=8",
        "--filter-print-omitted",
        "--count",
        "HEAD",
    )
    assert blob_limit == f"~{large}\n5\n".encode()
