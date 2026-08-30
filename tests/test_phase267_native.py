from __future__ import annotations

import subprocess

import pytest


def _git(cwd, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout.strip()


def test_native_git_sha256_ordered_blob_limit_threshold_and_count(tmp_path):
    repo = tmp_path / "native"
    repo.mkdir()
    try:
        _git(repo, "init", "--object-format=sha256", ".")
    except subprocess.CalledProcessError as exc:
        pytest.skip(f"native Git lacks SHA-256 repository support: {exc.stderr}")

    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")

    (repo / "small.bin").write_bytes(b"sss")
    _git(repo, "add", "small.bin")
    _git(repo, "commit", "-m", "small")
    c1 = _git(repo, "rev-parse", "HEAD")

    (repo / "large.bin").write_bytes(b"LLLLLLLL")
    _git(repo, "add", "large.bin")
    _git(repo, "commit", "-m", "large")
    c2 = _git(repo, "rev-parse", "HEAD")

    tree1 = _git(repo, "rev-parse", f"{c1}^{{tree}}")
    tree2 = _git(repo, "rev-parse", f"{c2}^{{tree}}")
    small_blob = _git(repo, "rev-parse", f"{c2}:small.bin")
    large_blob = _git(repo, "rev-parse", f"{c2}:large.bin")

    lines = _git(
        repo,
        "rev-list",
        "--objects",
        "--in-commit-order",
        "--filter=blob:limit=8",
        "--no-object-names",
        "HEAD",
    ).splitlines()
    assert lines == [c2, tree2, small_blob, c1, tree1]
    assert large_blob not in lines

    count = _git(
        repo,
        "rev-list",
        "--objects",
        "--in-commit-order",
        "--filter=blob:limit=8",
        "--count",
        "HEAD",
    )
    assert count == "5"
