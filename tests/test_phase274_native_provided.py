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


def _env(timestamp: int):
    env = os.environ.copy()
    value = f"@{timestamp} +0000"
    env["GIT_AUTHOR_DATE"] = value
    env["GIT_COMMITTER_DATE"] = value
    return env


def _require_git_255():
    version = _git(None, "--version")
    match = re.search(r"(\d+)\.(\d+)", version)
    if match is None or (int(match.group(1)), int(match.group(2))) < (2, 55):
        pytest.skip("native probe requires Git >= 2.55")


def test_native_sha256_annotated_tag_is_provided_for_existing_object_filters(tmp_path):
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
    _git(repo, "commit", "-qm", "c1", env=_env(1))
    commit = _git(repo, "rev-parse", "HEAD")
    tree = _git(repo, "rev-parse", "HEAD^{tree}")
    blob = _git(repo, "rev-parse", "HEAD:f.txt")
    _git(repo, "tag", "-a", "-m", "release", "v1", "HEAD", env=_env(2))
    tag = _git(repo, "rev-parse", "v1^{tag}")

    expected = {
        "commit": [commit, tag],
        "tree": [commit, tag, tree],
        "blob": [commit, tag, blob],
    }
    for requested in ("commit", "tree", "blob"):
        assert _git(
            repo,
            "rev-list",
            "--objects",
            f"--filter=object:type={requested}",
            "--no-object-names",
            "v1",
        ).splitlines() == expected[requested]

        filtered = _git(
            repo,
            "rev-list",
            "--objects",
            f"--filter=object:type={requested}",
            "--filter-provided-objects",
            "--no-object-names",
            "v1",
        ).splitlines()
        assert tag not in filtered
        assert len(filtered) == 1
