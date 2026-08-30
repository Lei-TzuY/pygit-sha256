from __future__ import annotations

import hashlib

import pytest

from pygit.fetch_importer import PromisorFilteredNativeImporter
from pygit.objects import CommitObject, TreeObject
from pygit.promisor import read_promisor_state
from pygit.remote import NativeObject
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


def _fields(output: str):
    fields = output.split("\0")
    assert fields[-1] == ""
    return fields[:-1]


def _native_oid(type_name: str, data: bytes) -> str:
    return hashlib.sha1(f"{type_name} {len(data)}\0".encode() + data).hexdigest()


def _partial_repo(tmp_path):
    repo = Repository.init(str(tmp_path / "partial"))
    repo.add_remote("origin", "https://example.test/repo.git")

    blob = _native_oid("blob", b"promised payload\n")
    tree_data = b"100644 f.txt\0" + bytes.fromhex(blob)
    tree = _native_oid("tree", tree_data)
    commit_data = (
        f"tree {tree}\n"
        "author Test <test@example.com> 1 +0000\n"
        "committer Test <test@example.com> 1 +0000\n"
        "\nmsg\n"
    ).encode()
    commit = _native_oid("commit", commit_data)

    importer = PromisorFilteredNativeImporter(
        repo.store,
        {
            tree: NativeObject("tree", tree_data, tree),
            commit: NativeObject("commit", commit_data, commit),
        },
        remote="origin",
        filter_spec="blob:none",
    )
    local_commit = importer.import_oid(commit)
    repo.refs.set_branch("main", local_commit, message="test: partial tip")
    repo.refs.set_head_symbolic("main", message="test: partial tip")
    return repo, local_commit, blob


def _disable_fetch(monkeypatch):
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_object",
        lambda *args, **kwargs: pytest.fail("ordered blob-limit -z must not single-fetch"),
    )
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_objects",
        lambda *args, **kwargs: pytest.fail("ordered blob-limit -z must not batch-fetch"),
    )


def test_ordered_blob_limit_nul_preserves_first_seen_order(
    tmp_path, monkeypatch, capsys
):
    repo, c1, c2 = _repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    tree1, blobs1 = _snapshot(repo, c1)
    tree2, blobs2 = _snapshot(repo, c2)
    assert blobs1["small.bin"] == blobs2["small.bin"]
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "-z",
            "--filter=blob:limit=8",
            "--no-object-names",
            "HEAD",
        ]
    ) == 0

    output = capsys.readouterr().out
    assert _fields(output) == [
        c2,
        tree2,
        blobs2["small.bin"],
        c1,
        tree1,
    ]
    assert blobs2["large.bin"] not in output


def test_ordered_blob_limit_nul_preserves_path_metadata(
    tmp_path, monkeypatch, capsys
):
    repo, _c1, c2 = _repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _tree2, blobs2 = _snapshot(repo, c2)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        ["--objects", "--in-commit-order", "-z", "--filter=blob:limit=8", "HEAD"]
    ) == 0
    fields = _fields(capsys.readouterr().out)

    assert blobs2["small.bin"] in fields
    assert "path=small.bin" in fields
    assert blobs2["large.bin"] not in fields
    assert "path=large.bin" not in fields


def test_ordered_blob_limit_nul_reverse_changes_first_seen_positions(
    tmp_path, monkeypatch, capsys
):
    repo, c1, c2 = _repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    tree1, blobs1 = _snapshot(repo, c1)
    tree2, blobs2 = _snapshot(repo, c2)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "--reverse",
            "-z",
            "--filter=blob:limit=8",
            "--no-object-names",
            "HEAD",
        ]
    ) == 0

    output = capsys.readouterr().out
    assert _fields(output) == [
        c1,
        tree1,
        blobs1["small.bin"],
        c2,
        tree2,
    ]
    assert blobs2["large.bin"] not in output


def test_ordered_blob_limit_nul_boundary_uses_structured_metadata(
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
            "--no-object-names",
            "HEAD",
        ]
    ) == 0

    assert _fields(capsys.readouterr().out) == [
        c2,
        tree2,
        blobs2["small.bin"],
        c1,
        "boundary=yes",
        tree1,
    ]


def test_ordered_blob_limit_nul_count_is_newline_integer(
    tmp_path, monkeypatch, capsys
):
    repo, _c1, _c2 = _repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "-z",
            "--filter=blob:limit=8",
            "--count",
            "HEAD",
        ]
    ) == 0

    assert capsys.readouterr().out == "5\n"


def test_ordered_blob_limit_nul_filter_provided_keeps_same_blob_membership(
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
            "--filter-provided-objects",
            "--no-object-names",
            "HEAD",
        ]
    ) == 0

    assert _fields(capsys.readouterr().out) == [
        c2,
        tree2,
        blobs2["small.bin"],
        c1,
        tree1,
    ]


def test_ordered_blob_limit_nul_refuses_unresolved_promised_blob_before_output(
    tmp_path, monkeypatch, capsys
):
    repo, _commit, native_blob = _partial_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    before = read_promisor_state(repo.pygit_dir)
    capsys.readouterr()

    with pytest.raises(RuntimeError, match="persistent promisor size metadata is unavailable"):
        run_rev_list_disk_usage(
            [
                "--objects",
                "--in-commit-order",
                "-z",
                "--filter=blob:limit=1k",
                "--missing=print-info",
                "HEAD",
            ]
        )

    assert capsys.readouterr().out == ""
    assert read_promisor_state(repo.pygit_dir) == before
    assert native_blob in before["promised"]


def test_ordered_blob_limit_nul_retains_objects_edge_guard(
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
                f"{c1}..{c2}",
            ]
        )
