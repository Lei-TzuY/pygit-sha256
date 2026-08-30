from __future__ import annotations

import shutil
import subprocess

import pytest

from pygit.objects import CommitObject, TreeObject
from pygit.repo import Repository
from pygit.rev_list_disk_usage_cli import run_rev_list_disk_usage


def _repo(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    (repo.worktree / "small.bin").write_bytes(b"sss")
    repo.add(["small.bin"])
    c1 = repo.commit(
        "small",
        author_name="Test",
        author_email="test@example.com",
        commit_date="1",
    )
    (repo.worktree / "large.bin").write_bytes(b"LLLLLLLL")
    repo.add(["large.bin"])
    c2 = repo.commit(
        "large",
        author_name="Test",
        author_email="test@example.com",
        commit_date="2",
    )
    return repo, c1, c2


def _snapshot(repo: Repository, commit_oid: str):
    commit = repo.store.read(commit_oid)
    assert isinstance(commit, CommitObject)
    tree = repo.store.read(commit.tree)
    assert isinstance(tree, TreeObject)
    blobs = {entry.name: entry.sha.lower() for entry in tree.entries}
    return commit.tree.lower(), blobs


def _route_repo(monkeypatch, repo: Repository):
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    monkeypatch.setattr("pygit.rev_list_disk_usage_cli._find_repo", lambda: repo)


def _unit_sizes(monkeypatch, seen=None):
    def size(_repo, oid):
        if seen is not None:
            seen.append(oid)
        return 1

    monkeypatch.setattr("pygit.rev_list_disk_usage_cli.object_disk_size", size)


def test_ordered_disk_usage_counts_selected_commits_without_printing_traversal(
    tmp_path, monkeypatch, capsys
):
    repo, _c1, _c2 = _repo(tmp_path)
    _route_repo(monkeypatch, repo)
    _unit_sizes(monkeypatch)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        ["--in-commit-order", "--disk-usage", "HEAD"]
    ) == 0
    assert capsys.readouterr().out == "2\n"


def test_ordered_objects_disk_usage_reuses_full_current_stack_selection(
    tmp_path, monkeypatch, capsys
):
    repo, _c1, _c2 = _repo(tmp_path)
    _route_repo(monkeypatch, repo)
    _unit_sizes(monkeypatch)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        ["--objects", "--in-commit-order", "--disk-usage", "HEAD"]
    ) == 0
    assert capsys.readouterr().out == "6\n"


def test_ordered_disk_usage_z_and_reverse_are_aggregate_presentation_noops(
    tmp_path, monkeypatch, capsys
):
    repo, _c1, _c2 = _repo(tmp_path)
    _route_repo(monkeypatch, repo)
    _unit_sizes(monkeypatch)

    for extra in (["-z"], ["--reverse"], ["-z", "--reverse"]):
        capsys.readouterr()
        assert run_rev_list_disk_usage(
            ["--objects", "--in-commit-order", "--disk-usage", *extra, "HEAD"]
        ) == 0
        assert capsys.readouterr().out == "6\n"


def test_ordered_disk_usage_count_is_zero_then_aggregate(
    tmp_path, monkeypatch, capsys
):
    repo, _c1, _c2 = _repo(tmp_path)
    _route_repo(monkeypatch, repo)
    _unit_sizes(monkeypatch)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        ["--in-commit-order", "--disk-usage", "--count", "HEAD"]
    ) == 0
    assert capsys.readouterr().out == "0\n2\n"


def test_ordered_object_edge_record_is_visible_but_not_sized(
    tmp_path, monkeypatch, capsys
):
    repo, c1, c2 = _repo(tmp_path)
    _route_repo(monkeypatch, repo)
    seen = []
    _unit_sizes(monkeypatch, seen)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects-edge",
            "--in-commit-order",
            "--disk-usage",
            f"{c1}..{c2}",
        ]
    ) == 0
    output = capsys.readouterr().out.splitlines()
    assert output[0] == f"-{c1}"
    assert output[-1].isdigit()
    assert c1 not in seen
    assert int(output[-1]) == len(seen)


def test_ordered_blob_limit_disk_usage_preserves_omission_channel(
    tmp_path, monkeypatch, capsys
):
    repo, _c1, c2 = _repo(tmp_path)
    _route_repo(monkeypatch, repo)
    _unit_sizes(monkeypatch)
    _tree2, blobs2 = _snapshot(repo, c2)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "--disk-usage",
            "-z",
            "--filter=blob:limit=8",
            "--filter-print-omitted",
            "HEAD",
        ]
    ) == 0
    assert capsys.readouterr().out == f"~{blobs2['large.bin']}\n5\n"


def test_native_sha256_git_ordered_disk_usage_protocol(tmp_path):
    git = shutil.which("git")
    if git is None:
        pytest.skip("native git not installed")

    repo = tmp_path / "native"
    init = subprocess.run(
        [git, "init", "--object-format=sha256", str(repo)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if init.returncode != 0:
        pytest.skip("native git lacks SHA-256 repository support")

    subprocess.run([git, "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(
        [git, "-C", str(repo), "config", "user.email", "test@example.com"],
        check=True,
    )
    (repo / "f").write_bytes(b"a")
    subprocess.run([git, "-C", str(repo), "add", "f"], check=True)
    subprocess.run([git, "-C", str(repo), "commit", "-m", "one"], check=True, stdout=subprocess.PIPE)
    (repo / "f").write_bytes(b"ab")
    subprocess.run([git, "-C", str(repo), "commit", "-am", "two"], check=True, stdout=subprocess.PIPE)

    plain = subprocess.run(
        [git, "-C", str(repo), "rev-list", "--in-commit-order", "--disk-usage", "-z", "HEAD"],
        check=True,
        stdout=subprocess.PIPE,
    ).stdout
    counted = subprocess.run(
        [
            git,
            "-C",
            str(repo),
            "rev-list",
            "--in-commit-order",
            "--disk-usage",
            "--count",
            "HEAD",
        ],
        check=True,
        stdout=subprocess.PIPE,
    ).stdout

    assert plain.endswith(b"\n")
    assert b"\0" not in plain
    assert plain[:-1].isdigit()
    count_line, total_line, empty = counted.split(b"\n")
    assert count_line == b"0"
    assert total_line.isdigit()
    assert empty == b""
