from __future__ import annotations

import os
import re
import subprocess

import pytest


def _git(cwd, *args, env=None, text=True):
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
    ).stdout


def _env(timestamp: int):
    env = os.environ.copy()
    value = f"@{timestamp} +0000"
    env["GIT_AUTHOR_DATE"] = value
    env["GIT_COMMITTER_DATE"] = value
    return env


def _require_git_255():
    version = _git(None, "--version").strip()
    match = re.search(r"(\d+)\.(\d+)", version)
    if match is None or (int(match.group(1)), int(match.group(2))) < (2, 55):
        pytest.skip("native structured rev-list probe requires Git >= 2.55")


def test_native_sha256_annotated_tag_nul_protocol(tmp_path):
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

    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-qm", "c1", env=_env(1))
    c1 = _git(repo, "rev-parse", "HEAD").strip()

    (repo / "b.txt").write_text("b\n", encoding="utf-8")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-qm", "c2", env=_env(2))
    c2 = _git(repo, "rev-parse", "HEAD").strip()

    _git(repo, "tag", "-a", "-m", "one", "v1", "HEAD", env=_env(3))
    tag1 = _git(repo, "rev-parse", "v1^{tag}").strip()
    _git(repo, "tag", "-a", "-m", "two", "v2", "v1", env=_env(4))
    tag2 = _git(repo, "rev-parse", "v2^{tag}").strip()

    ordinary = _git(
        repo,
        "rev-list",
        "--objects",
        "-z",
        "--filter=object:type=tag",
        "v2",
        text=False,
    )
    assert ordinary == (
        c2.encode()
        + b"\0"
        + tag2.encode()
        + b"\0path=v2\0"
        + tag1.encode()
        + b"\0path=v1\0"
    )

    filtered = _git(
        repo,
        "rev-list",
        "--objects",
        "-z",
        "--no-object-names",
        "--filter=object:type=tag",
        "--filter-provided-objects",
        "v2",
        text=False,
    )
    assert filtered == tag2.encode() + b"\0" + tag1.encode() + b"\0"

    counted = _git(
        repo,
        "rev-list",
        "--objects",
        "-z",
        "--count",
        "--filter=object:type=tag",
        "v2",
        text=False,
    )
    assert counted == b"3\n"

    boundary = _git(
        repo,
        "rev-list",
        "--objects",
        "--boundary",
        "--max-count=1",
        "-z",
        "--filter=object:type=commit",
        "v1",
        text=False,
    )
    assert boundary == (
        c2.encode()
        + b"\0"
        + c1.encode()
        + b"\0boundary=yes\0"
        + tag1.encode()
        + b"\0path=v1\0"
    )

    tree = _git(
        repo,
        "rev-list",
        "--objects",
        "-z",
        "--filter=object:type=tree",
        "v1",
        text=False,
    )
    assert tree.startswith(c2.encode() + b"\0" + tag1.encode() + b"\0path=v1\0")
    assert b"\n" not in tree

    assert len(c1) == len(c2) == len(tag1) == len(tag2) == 64
