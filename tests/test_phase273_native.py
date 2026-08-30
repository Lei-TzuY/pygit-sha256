from __future__ import annotations

import os
import re
import subprocess

import pytest


def _git(cwd, *args, env=None):
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


def _commit_env(timestamp: int):
    env = os.environ.copy()
    value = f"@{timestamp} +0000"
    env["GIT_AUTHOR_DATE"] = value
    env["GIT_COMMITTER_DATE"] = value
    return env


def _require_git_255():
    version = _git(None, "--version")
    match = re.search(r"(\d+)\.(\d+)", version)
    if match is None:
        pytest.skip(f"cannot parse Git version: {version}")
    if (int(match.group(1)), int(match.group(2))) < (2, 55):
        pytest.skip("native probe requires Git >= 2.55")


def test_native_sha256_object_type_tag_provided_and_nested_semantics(tmp_path):
    _require_git_255()
    repo = tmp_path / "native"
    subprocess.run(
        ["git", "init", "--object-format=sha256", "-q", str(repo)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")

    (repo / "f.txt").write_text("payload\n", encoding="utf-8")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-qm", "c1", env=_commit_env(1))
    commit = _git(repo, "rev-parse", "HEAD")

    _git(repo, "tag", "-a", "-m", "one", "v1", "HEAD", env=_commit_env(2))
    tag1 = _git(repo, "rev-parse", "v1^{tag}")
    _git(repo, "tag", "-a", "-m", "two", "v2", "v1", env=_commit_env(3))
    tag2 = _git(repo, "rev-parse", "v2^{tag}")

    assert _git(
        repo,
        "rev-list",
        "--objects",
        "--filter=object:type=tag",
        "--no-object-names",
        "v2",
    ).splitlines() == [commit, tag2, tag1]

    assert _git(
        repo,
        "rev-list",
        "--objects",
        "--filter=object:type=tag",
        "--filter-provided-objects",
        "--no-object-names",
        "v2",
    ).splitlines() == [tag2, tag1]

    assert _git(
        repo,
        "rev-list",
        "--objects",
        "--filter=object:type=tag",
        "v2",
    ).splitlines() == [commit, f"{tag2} v2", f"{tag1} v1"]

    assert _git(
        repo,
        "rev-list",
        "--objects",
        "--filter=object:type=tag",
        "--count",
        "--no-object-names",
        "v2",
    ) == "3"
    assert _git(
        repo,
        "rev-list",
        "--objects",
        "--filter=object:type=tag",
        "--filter-provided-objects",
        "--count",
        "--no-object-names",
        "v2",
    ) == "2"

    assert _git(
        repo,
        "rev-list",
        "--objects",
        "--filter=object:type=tag",
        "--no-object-names",
        "v1^{}",
    ).splitlines() == [commit]

    assert _git(
        repo,
        "rev-list",
        "--objects",
        "--filter=object:type=tag",
        "--no-object-names",
        "--all",
    ).splitlines() == [commit, tag1, tag2]
