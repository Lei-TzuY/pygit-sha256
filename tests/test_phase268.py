from __future__ import annotations

import hashlib

import pytest

from pygit.fetch_importer import PromisorFilteredNativeImporter
from pygit.objects import CommitObject, TreeObject
from pygit.promisor import read_promisor_state
from pygit.remote import NativeObject
from pygit.repo import Repository
from pygit.rev_list_disk_usage_cli import run_rev_list_disk_usage


def _ordinary_repo(tmp_path):
    repo = Repository.init(str(tmp_path / "ordinary"))
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
    return repo, blob


def _disable_fetch(monkeypatch):
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_object",
        lambda *args, **kwargs: pytest.fail("blob:limit omitted must not single-fetch"),
    )
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_objects",
        lambda *args, **kwargs: pytest.fail("blob:limit omitted must not batch-fetch"),
    )


def test_ordered_blob_limit_prints_omitted_after_traversal(tmp_path, monkeypatch, capsys):
    repo, c1, c2 = _ordinary_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    tree1, _ = _snapshot(repo, c1)
    tree2, blobs2 = _snapshot(repo, c2)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "--filter=blob:limit=8",
            "--filter-print-omitted",
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
        f"~{blobs2['large.bin']}",
    ]


def test_ordered_blob_limit_reverse_keeps_omissions_after_ordered_stream(
    tmp_path, monkeypatch, capsys
):
    repo, c1, c2 = _ordinary_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    tree1, blobs1 = _snapshot(repo, c1)
    tree2, blobs2 = _snapshot(repo, c2)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "--reverse",
            "--filter=blob:limit=8",
            "--filter-print-omitted",
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
        f"~{blobs2['large.bin']}",
    ]


def test_ordered_blob_limit_omitted_precedes_filtered_count(tmp_path, monkeypatch, capsys):
    repo, _c1, c2 = _ordinary_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _tree2, blobs2 = _snapshot(repo, c2)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "--filter=blob:limit=8",
            "--filter-print-omitted",
            "--count",
            "HEAD",
        ]
    ) == 0
    assert capsys.readouterr().out.splitlines() == [f"~{blobs2['large.bin']}", "5"]


def test_ordered_blob_limit_boundary_traversal_precedes_omitted(
    tmp_path, monkeypatch, capsys
):
    repo, c1, c2 = _ordinary_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    tree1, _ = _snapshot(repo, c1)
    tree2, blobs2 = _snapshot(repo, c2)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "--boundary",
            "--max-count=1",
            "--filter=blob:limit=8",
            "--filter-print-omitted",
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
        f"~{blobs2['large.bin']}",
    ]


def test_ordered_blob_limit_edge_stays_first_and_omission_stays_last(
    tmp_path, monkeypatch, capsys
):
    repo, c1, c2 = _ordinary_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    tree2, blobs2 = _snapshot(repo, c2)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects-edge",
            "--in-commit-order",
            "--filter=blob:limit=8",
            "--filter-print-omitted",
            "--no-object-names",
            f"{c1}..{c2}",
        ]
    ) == 0
    assert capsys.readouterr().out.splitlines() == [
        f"-{c1}",
        c2,
        tree2,
        f"~{blobs2['large.bin']}",
    ]


def test_ordered_blob_limit_omitted_ids_are_genuine_local_sha256(
    tmp_path, monkeypatch, capsys
):
    repo, _c1, c2 = _ordinary_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _tree2, blobs2 = _snapshot(repo, c2)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "--filter=blob:limit=0",
            "--filter-print-omitted",
            "--no-object-names",
            "HEAD",
        ]
    ) == 0
    omitted = [line[1:] for line in capsys.readouterr().out.splitlines() if line.startswith("~")]
    assert set(omitted) == set(blobs2.values())
    assert omitted
    assert all(len(oid) == 64 and all(ch in "0123456789abcdef" for ch in oid) for oid in omitted)


def test_ordered_blob_limit_omitted_refuses_unresolved_blob_before_output(
    tmp_path, monkeypatch, capsys
):
    repo, native_blob = _partial_repo(tmp_path)
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
                "--filter-print-omitted",
                "--missing=allow-promisor",
                "HEAD",
            ]
        )

    assert capsys.readouterr().out == ""
    assert native_blob in before["promised"]
    assert read_promisor_state(repo.pygit_dir) == before
