from __future__ import annotations

import hashlib

import pytest

from pygit.fetch_importer import PromisorFilteredNativeImporter
from pygit.objects import BlobObject, CommitObject, TreeObject
from pygit.promisor import read_promisor_state
from pygit.remote import NativeObject
from pygit.repo import Repository
from pygit.rev_list_disk_usage_cli import run_rev_list_disk_usage


def _ordinary_two_commit_repo(tmp_path, *, small_size=3, large_size=8):
    repo = Repository.init(str(tmp_path / "ordinary"))
    (repo.worktree / "small.bin").write_bytes(b"s" * small_size)
    repo.add(["small.bin"])
    c1 = repo.commit(
        "small",
        author_name="Test",
        author_email="test@example.com",
        commit_date="1",
    )

    (repo.worktree / "large.bin").write_bytes(b"L" * large_size)
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
    blobs = {
        entry.name: entry.sha.lower()
        for entry in tree.entries
        if entry.mode in {"100644", "100755", "120000"}
    }
    return commit.tree.lower(), blobs


def _local_blobs_from_output(repo: Repository, output: str):
    result = {}
    for line in output.splitlines():
        if not line:
            continue
        token = line.split(None, 1)[0].lstrip("-")
        if len(token) != 64:
            continue
        obj = repo.store.read(token)
        if isinstance(obj, BlobObject):
            result[len(obj)] = token
    return result


def _native_oid(type_name: str, data: bytes) -> str:
    return hashlib.sha1(f"{type_name} {len(data)}\0".encode() + data).hexdigest()


def _partial_blob_none_repo(tmp_path):
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
        lambda *args, **kwargs: pytest.fail("blob:limit must not single-fetch"),
    )
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_objects",
        lambda *args, **kwargs: pytest.fail("blob:limit must not batch-fetch"),
    )


def test_current_stack_port_preserves_general_blob_limit_threshold(tmp_path, monkeypatch, capsys):
    repo, _c1, c2 = _ordinary_two_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _tree2, blobs2 = _snapshot(repo, c2)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--filter=blob:limit=8",
            "--missing=print-info",
            "--no-object-names",
            "HEAD",
        ]
    ) == 0
    out = capsys.readouterr().out
    assert blobs2["small.bin"] in out
    assert blobs2["large.bin"] not in out


def test_current_stack_port_preserves_general_blob_limit_count(tmp_path, monkeypatch, capsys):
    repo, _c1, _c2 = _ordinary_two_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    args = [
        "--objects",
        "--filter=blob:limit=8",
        "--missing=print-info",
        "--no-object-names",
        "HEAD",
    ]
    assert run_rev_list_disk_usage(args) == 0
    surviving = [line for line in capsys.readouterr().out.splitlines() if line]

    assert run_rev_list_disk_usage([*args[:-1], "--count", args[-1]]) == 0
    assert capsys.readouterr().out.splitlines() == [str(len(surviving))]


def test_ordered_blob_limit_preserves_first_seen_commit_snapshot_order(
    tmp_path, monkeypatch, capsys
):
    repo, c1, c2 = _ordinary_two_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    tree1, blobs1 = _snapshot(repo, c1)
    tree2, blobs2 = _snapshot(repo, c2)
    assert blobs1["small.bin"] == blobs2["small.bin"]
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "--filter=blob:limit=8",
            "--no-object-names",
            "HEAD",
        ]
    ) == 0
    assert capsys.readouterr().out.splitlines() == [
        c2,
        tree2,
        blobs2["small.bin"],
        c1,
        tree1,
    ]


def test_ordered_blob_limit_reverse_changes_first_seen_positions(tmp_path, monkeypatch, capsys):
    repo, c1, c2 = _ordinary_two_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    tree1, blobs1 = _snapshot(repo, c1)
    tree2, _blobs2 = _snapshot(repo, c2)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "--reverse",
            "--filter=blob:limit=8",
            "--no-object-names",
            "HEAD",
        ]
    ) == 0
    assert capsys.readouterr().out.splitlines() == [
        c1,
        tree1,
        blobs1["small.bin"],
        c2,
        tree2,
    ]


def test_ordered_blob_limit_boundary_keeps_boundary_snapshot_order(tmp_path, monkeypatch, capsys):
    repo, c1, c2 = _ordinary_two_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    tree1, _blobs1 = _snapshot(repo, c1)
    tree2, blobs2 = _snapshot(repo, c2)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "--boundary",
            "--max-count=1",
            "--filter=blob:limit=8",
            "--no-object-names",
            "HEAD",
        ]
    ) == 0
    assert capsys.readouterr().out.splitlines() == [
        c2,
        tree2,
        blobs2["small.bin"],
        f"-{c1}",
        tree1,
    ]


def test_ordered_blob_limit_object_edge_keeps_edge_outside_filter(tmp_path, monkeypatch, capsys):
    repo, c1, c2 = _ordinary_two_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    tree2, _blobs2 = _snapshot(repo, c2)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects-edge",
            "--in-commit-order",
            "--filter=blob:limit=8",
            "--no-object-names",
            f"{c1}..{c2}",
        ]
    ) == 0
    assert capsys.readouterr().out.splitlines() == [f"-{c1}", c2, tree2]


def test_ordered_blob_limit_count_counts_only_surviving_present_objects(
    tmp_path, monkeypatch, capsys
):
    repo, _c1, _c2 = _ordinary_two_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "--filter=blob:limit=8",
            "--count",
            "HEAD",
        ]
    ) == 0
    assert capsys.readouterr().out.splitlines() == ["5"]


def test_ordered_blob_limit_zero_and_binary_suffix_follow_git_units(tmp_path, monkeypatch, capsys):
    repo, c1, c2 = _ordinary_two_commit_repo(
        tmp_path,
        small_size=1023,
        large_size=1024,
    )
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    tree1, blobs1 = _snapshot(repo, c1)
    tree2, blobs2 = _snapshot(repo, c2)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "--filter=blob:limit=1k",
            "--no-object-names",
            "HEAD",
        ]
    ) == 0
    assert capsys.readouterr().out.splitlines() == [
        c2,
        tree2,
        blobs2["small.bin"],
        c1,
        tree1,
    ]
    assert blobs2["large.bin"] not in blobs1.values()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "--filter=blob:limit=0",
            "--no-object-names",
            "HEAD",
        ]
    ) == 0
    assert capsys.readouterr().out.splitlines() == [c2, tree2, c1, tree1]


def test_ordered_blob_limit_accepts_filter_provided_for_commit_roots(
    tmp_path, monkeypatch, capsys
):
    repo, c1, c2 = _ordinary_two_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    tree1, _ = _snapshot(repo, c1)
    tree2, blobs2 = _snapshot(repo, c2)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "--filter=blob:limit=8",
            "--filter-provided-objects",
            "--no-object-names",
            "HEAD",
        ]
    ) == 0
    assert capsys.readouterr().out.splitlines() == [
        c2,
        tree2,
        blobs2["small.bin"],
        c1,
        tree1,
    ]


def test_ordered_blob_limit_refuses_unresolved_promised_blob_without_fetch(
    tmp_path, monkeypatch, capsys
):
    repo, _commit, native_blob = _partial_blob_none_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    before = read_promisor_state(repo.pygit_dir)
    capsys.readouterr()

    with pytest.raises(RuntimeError, match="persistent promisor size metadata is unavailable"):
        run_rev_list_disk_usage(
            [
                "--objects",
                "--in-commit-order",
                "--filter=blob:limit=1k",
                "HEAD",
            ]
        )

    assert capsys.readouterr().out == ""
    assert read_promisor_state(repo.pygit_dir) == before
    assert native_blob in before["promised"]


def test_ordered_blob_limit_rejects_invalid_and_deferred_framing(tmp_path, monkeypatch):
    repo, _c1, _c2 = _ordinary_two_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)

    with pytest.raises(ValueError, match=r"requires <n>\[kmg\]"):
        run_rev_list_disk_usage(
            ["--objects", "--in-commit-order", "--filter=blob:limit=1t", "HEAD"]
        )
    with pytest.raises(ValueError, match="and -z is not yet supported"):
        run_rev_list_disk_usage(
            ["--objects", "--in-commit-order", "-z", "--filter=blob:limit=8", "HEAD"]
        )
    with pytest.raises(ValueError, match="with --disk-usage"):
        run_rev_list_disk_usage(
            [
                "--objects",
                "--in-commit-order",
                "--filter=blob:limit=8",
                "--disk-usage",
                "HEAD",
            ]
        )
