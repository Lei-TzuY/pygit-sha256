from __future__ import annotations

import re
import shutil
import subprocess

import pytest


def _git_version(git: str) -> tuple[int, int, int]:
    text = subprocess.check_output([git, "--version"], text=True).strip()
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
    if match is None:
        return (0, 0, 0)
    return tuple(int(part) for part in match.groups())


def _size(git: str, repo, oid: str) -> int:
    out = subprocess.check_output(
        [git, "cat-file", "--batch-check=%(objectsize:disk)"],
        cwd=repo,
        input=oid + "\n",
        text=True,
    )
    return int(out.strip())


def test_native_git_255_sha256_annotated_tag_disk_usage_protocol(tmp_path):
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not installed")
    if _git_version(git) < (2, 55, 0):
        pytest.skip("structured compatibility target is Git 2.55+")

    repo = tmp_path / "native"
    probe = subprocess.run(
        [git, "init", "--object-format=sha256", str(repo)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if probe.returncode != 0:
        pytest.skip("native git lacks SHA-256 repository support")

    subprocess.run([git, "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run([git, "config", "user.email", "test@example.com"], cwd=repo, check=True)

    (repo / "f.txt").write_text("one\n", encoding="utf-8")
    subprocess.run([git, "add", "f.txt"], cwd=repo, check=True)
    subprocess.run([git, "commit", "-m", "c1"], cwd=repo, stdout=subprocess.PIPE, check=True)
    c1 = subprocess.check_output([git, "rev-parse", "HEAD"], cwd=repo, text=True).strip()

    (repo / "f.txt").write_text("two\n", encoding="utf-8")
    subprocess.run([git, "add", "f.txt"], cwd=repo, check=True)
    subprocess.run([git, "commit", "-m", "c2"], cwd=repo, stdout=subprocess.PIPE, check=True)
    c2 = subprocess.check_output([git, "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    subprocess.run([git, "tag", "-a", "v1", "-m", "release"], cwd=repo, check=True)
    tag = subprocess.check_output([git, "rev-parse", "v1"], cwd=repo, text=True).strip()

    commit_total = _size(git, repo, c2) + _size(git, repo, c1) + _size(git, repo, tag)
    tag_total = _size(git, repo, c2) + _size(git, repo, tag)
    tag_filtered_total = _size(git, repo, tag)

    tag_out = subprocess.check_output(
        [git, "rev-list", "--objects", "--filter=object:type=tag", "--disk-usage", "v1"],
        cwd=repo,
        text=True,
    )
    assert tag_out == f"{tag_total}\n"

    tag_filtered_out = subprocess.check_output(
        [
            git,
            "rev-list",
            "--objects",
            "--filter=object:type=tag",
            "--filter-provided-objects",
            "--disk-usage",
            "v1",
        ],
        cwd=repo,
        text=True,
    )
    assert tag_filtered_out == f"{tag_filtered_total}\n"

    commit_out = subprocess.check_output(
        [git, "rev-list", "--objects", "--filter=object:type=commit", "--disk-usage", "v1"],
        cwd=repo,
        text=True,
    )
    assert commit_out == f"{commit_total}\n"

    count_out = subprocess.check_output(
        [
            git,
            "rev-list",
            "--objects",
            "--count",
            "--filter=object:type=tag",
            "--disk-usage",
            "v1",
        ],
        cwd=repo,
        text=True,
    )
    assert count_out == f"0\n{tag_total}\n"

    boundary_out = subprocess.check_output(
        [
            git,
            "rev-list",
            "--objects",
            "--boundary",
            "--max-count=1",
            "--filter=object:type=commit",
            "--disk-usage",
            "v1",
        ],
        cwd=repo,
        text=True,
    )
    assert boundary_out == f"{commit_total}\n"

    edge_out = subprocess.check_output(
        [
            git,
            "rev-list",
            "--objects-edge",
            "--filter=object:type=tag",
            "--disk-usage",
            f"{c1}..v1",
        ],
        cwd=repo,
        text=True,
    )
    assert edge_out.splitlines() == [f"-{c1}", str(tag_total)]

    human_out = subprocess.check_output(
        [
            git,
            "rev-list",
            "--objects",
            "--filter=object:type=tag",
            "--disk-usage=human",
            "v1",
        ],
        cwd=repo,
        text=True,
    )
    assert human_out.endswith(" bytes\n")

    rejected = subprocess.run(
        [
            git,
            "rev-list",
            "--objects",
            "-z",
            "--filter=object:type=tag",
            "--disk-usage",
            "v1",
        ],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    assert rejected.returncode != 0
    assert "unsupported option" in rejected.stderr
