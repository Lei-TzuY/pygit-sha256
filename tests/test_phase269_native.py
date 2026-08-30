from __future__ import annotations

import os
import re
import subprocess

import pytest


def _git_text(cwd, *args, env=None):
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout.strip()


def _git_bytes(cwd, *args, env=None):
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        check=True,
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


def _require_modern_nul_protocol():
    version = subprocess.run(
        ["git", "--version"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout
    match = re.search(r"(\d+)\.(\d+)", version)
    if match is None:
        pytest.skip(f"cannot parse Git version: {version.strip()}")
    if (int(match.group(1)), int(match.group(2))) < (2, 55):
        pytest.skip("native Git predates the structured rev-list -z protocol")


def _strip_omitted_suffix(output: bytes, oid: str):
    suffix = b"~" + oid.encode() + b"\n"
    assert output.endswith(suffix)
    return output[: -len(suffix)]


def test_native_sha256_ordered_blob_limit_omitted_nul_framing(tmp_path):
    _require_modern_nul_protocol()

    repo = tmp_path / "native"
    subprocess.run(
        ["git", "init", "--object-format=sha256", "-q", str(repo)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    _git_text(repo, "config", "user.name", "Test")
    _git_text(repo, "config", "user.email", "test@example.com")

    (repo / "small.bin").write_bytes(b"sss")
    _git_text(repo, "add", "small.bin")
    _git_text(repo, "commit", "-qm", "small", env=_commit_env(1))
    c1 = _git_text(repo, "rev-parse", "HEAD")
    tree1 = _git_text(repo, "rev-parse", "HEAD^{tree}")
    small = _git_text(repo, "rev-parse", "HEAD:small.bin")

    (repo / "large.bin").write_bytes(b"LLLLLLLL")
    _git_text(repo, "add", "large.bin")
    _git_text(repo, "commit", "-qm", "large", env=_commit_env(2))
    c2 = _git_text(repo, "rev-parse", "HEAD")
    tree2 = _git_text(repo, "rev-parse", "HEAD^{tree}")
    large = _git_text(repo, "rev-parse", "HEAD:large.bin")

    normal = _git_bytes(
        repo,
        "rev-list",
        "--objects",
        "--in-commit-order",
        "-z",
        "--filter=blob:limit=8",
        "--filter-print-omitted",
        "--no-object-names",
        "HEAD",
    )
    normal_traversal = _strip_omitted_suffix(normal, large)
    assert [field.decode() for field in normal_traversal.split(b"\0") if field] == [
        c2,
        tree2,
        small,
        c1,
        tree1,
    ]
    assert b"omitted=yes" not in normal

    boundary = _git_bytes(
        repo,
        "rev-list",
        "--objects",
        "--in-commit-order",
        "-z",
        "--boundary",
        "--max-count=1",
        "--filter=blob:limit=8",
        "--filter-print-omitted",
        "--no-object-names",
        "HEAD",
    )
    boundary_traversal = _strip_omitted_suffix(boundary, large)
    assert [field.decode() for field in boundary_traversal.split(b"\0") if field] == [
        c2,
        tree2,
        small,
        c1,
        "boundary=yes",
        tree1,
    ]
