from __future__ import annotations

import os
import subprocess


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


def test_native_sha256_ordered_blob_limit_omitted_framing(tmp_path):
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
    c1 = _git(repo, "rev-parse", "HEAD")
    tree1 = _git(repo, "rev-parse", "HEAD^{tree}")
    small = _git(repo, "rev-parse", "HEAD:small.bin")

    (repo / "large.bin").write_bytes(b"LLLLLLLL")
    _git(repo, "add", "large.bin")
    _git(repo, "commit", "-qm", "large", env=_commit_env(2))
    c2 = _git(repo, "rev-parse", "HEAD")
    tree2 = _git(repo, "rev-parse", "HEAD^{tree}")
    large = _git(repo, "rev-parse", "HEAD:large.bin")

    normal = _git(
        repo,
        "rev-list",
        "--objects",
        "--in-commit-order",
        "--filter=blob:limit=8",
        "--filter-print-omitted",
        "--no-object-names",
        "HEAD",
    ).splitlines()
    assert normal == [c2, tree2, small, c1, tree1, f"~{large}"]

    counted = _git(
        repo,
        "rev-list",
        "--objects",
        "--in-commit-order",
        "--filter=blob:limit=8",
        "--filter-print-omitted",
        "--count",
        "HEAD",
    ).splitlines()
    assert counted == [f"~{large}", "5"]

    boundary = _git(
        repo,
        "rev-list",
        "--objects",
        "--in-commit-order",
        "--boundary",
        "--max-count=1",
        "--filter=blob:limit=8",
        "--filter-print-omitted",
        "--no-object-names",
        "HEAD",
    ).splitlines()
    assert boundary == [c2, tree2, small, f"-{c1}", tree1, f"~{large}"]

    edge = _git(
        repo,
        "rev-list",
        "--objects-edge",
        "--in-commit-order",
        "--filter=blob:limit=8",
        "--filter-print-omitted",
        "--no-object-names",
        f"{c1}..{c2}",
    ).splitlines()
    assert edge == [f"-{c1}", c2, tree2, f"~{large}"]
