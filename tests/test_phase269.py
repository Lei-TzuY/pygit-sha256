from __future__ import annotations

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


def _split_omitted_suffix(output: str):
    marker = output.find("~")
    assert marker >= 0
    return output[:marker], output[marker:]


def test_ordered_blob_limit_omitted_nul_uses_native_mixed_framing(
    tmp_path, monkeypatch, capsys
):
    repo, c1, c2 = _repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    tree1, _ = _snapshot(repo, c1)
    tree2, blobs2 = _snapshot(repo, c2)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "-z",
            "--filter=blob:limit=8",
            "--filter-print-omitted",
            "--no-object-names",
            "HEAD",
        ]
    ) == 0
    output = capsys.readouterr().out
    traversal, omitted = _split_omitted_suffix(output)

    assert traversal.split("\0") == [
        c2,
        tree2,
        blobs2["small.bin"],
        c1,
        tree1,
        "",
    ]
    assert omitted == f"~{blobs2['large.bin']}\n"
    assert "omitted=yes" not in output


def test_ordered_blob_limit_omitted_nul_preserves_path_metadata(
    tmp_path, monkeypatch, capsys
):
    repo, _c1, c2 = _repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _tree2, blobs2 = _snapshot(repo, c2)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "-z",
            "--filter=blob:limit=8",
            "--filter-print-omitted",
            "HEAD",
        ]
    ) == 0
    output = capsys.readouterr().out
    traversal, omitted = _split_omitted_suffix(output)

    fields = traversal.split("\0")
    assert blobs2["small.bin"] in fields
    assert "path=small.bin" in fields
    assert blobs2["large.bin"] not in fields
    assert omitted == f"~{blobs2['large.bin']}\n"


def test_ordered_blob_limit_omitted_nul_boundary_is_structured(
    tmp_path, monkeypatch, capsys
):
    repo, c1, c2 = _repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    tree1, _ = _snapshot(repo, c1)
    tree2, blobs2 = _snapshot(repo, c2)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "-z",
            "--boundary",
            "--max-count=1",
            "--filter=blob:limit=8",
            "--filter-print-omitted",
            "--no-object-names",
            "HEAD",
        ]
    ) == 0
    output = capsys.readouterr().out
    traversal, omitted = _split_omitted_suffix(output)

    assert traversal.split("\0") == [
        c2,
        tree2,
        blobs2["small.bin"],
        c1,
        "boundary=yes",
        tree1,
        "",
    ]
    assert omitted == f"~{blobs2['large.bin']}\n"
    assert f"-{c1}" not in output


def test_ordered_blob_limit_omitted_nul_retains_objects_edge_guard(
    tmp_path, monkeypatch
):
    repo, c1, c2 = _repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)

    with pytest.raises(ValueError, match="only compatible with --objects, --boundary, and --missing"):
        run_rev_list_disk_usage(
            [
                "--objects-edge",
                "--in-commit-order",
                "-z",
                "--filter=blob:limit=8",
                "--filter-print-omitted",
                f"{c1}..{c2}",
            ]
        )


def test_plain_ordered_blob_limit_nul_remains_a_separate_deferred_composition(
    tmp_path, monkeypatch
):
    repo, _c1, _c2 = _repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)

    with pytest.raises(ValueError, match="blob:limit and -z is not yet supported"):
        run_rev_list_disk_usage(
            [
                "--objects",
                "--in-commit-order",
                "-z",
                "--filter=blob:limit=8",
                "HEAD",
            ]
        )
