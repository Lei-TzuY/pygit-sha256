from __future__ import annotations

import hashlib

import pytest

from pygit.fetch_importer import PromisorFilteredNativeImporter
from pygit.objects import CommitObject, TreeObject
from pygit.promisor import read_promisor_state
from pygit.remote import NativeObject
from pygit.repo import Repository
from pygit.rev_list_disk_usage_cli import run_rev_list_disk_usage


def _ordinary_three_commit_repo(tmp_path):
    repo = Repository.init(str(tmp_path / "ordinary"))
    commits = []
    for index, name in enumerate(("a.txt", "b.txt", "c.txt"), start=1):
        (repo.worktree / name).write_text(f"{index}\n", encoding="utf-8")
        repo.add([name])
        commits.append(
            repo.commit(
                f"c{index}",
                author_name="Test",
                author_email="test@example.com",
                commit_date=str(index),
            )
        )
    return repo, tuple(commits)


def _snapshot(repo: Repository, commit_oid: str) -> tuple[str, tuple[str, ...]]:
    commit = repo.store.read(commit_oid)
    assert isinstance(commit, CommitObject)
    tree = repo.store.read(commit.tree)
    assert isinstance(tree, TreeObject)
    blobs = tuple(entry.sha.lower() for entry in sorted(tree.entries, key=lambda item: item.name))
    return commit.tree.lower(), blobs


def _native_oid(type_name: str, data: bytes) -> str:
    return hashlib.sha1(f"{type_name} {len(data)}\0".encode() + data).hexdigest()


def _partial_repo(tmp_path):
    repo = Repository.init(str(tmp_path / "partial"))
    repo.add_remote("origin", "https://example.test/repo.git")

    blob_oid = _native_oid("blob", b"payload\n")
    tree_data = b"100644 f.txt\0" + bytes.fromhex(blob_oid)
    tree_oid = _native_oid("tree", tree_data)
    commit_data = (
        f"tree {tree_oid}\n"
        "author Test <test@example.com> 1 +0000\n"
        "committer Test <test@example.com> 1 +0000\n"
        "\ntip\n"
    ).encode()
    commit_oid = _native_oid("commit", commit_data)

    importer = PromisorFilteredNativeImporter(
        repo.store,
        {
            tree_oid: NativeObject("tree", tree_data, tree_oid),
            commit_oid: NativeObject("commit", commit_data, commit_oid),
        },
        remote="origin",
        filter_spec="blob:none",
    )
    local_commit = importer.import_oid(commit_oid)
    repo.refs.set_branch("main", local_commit, message="test: partial tip")
    repo.refs.set_head_symbolic("main", message="test: partial tip")
    commit = repo.store.read(local_commit)
    assert isinstance(commit, CommitObject)
    return repo, local_commit, commit.tree.lower(), blob_oid


def _disable_fetch(monkeypatch):
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_object",
        lambda *args, **kwargs: pytest.fail("blob:none ordered traversal must not single-fetch"),
    )
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_objects",
        lambda *args, **kwargs: pytest.fail("blob:none ordered traversal must not batch-fetch"),
    )


def test_blob_none_preserves_ordered_commit_tree_stream_and_count(tmp_path, monkeypatch, capsys):
    repo, (c1, c2, c3) = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    tree1, blobs1 = _snapshot(repo, c1)
    tree2, blobs2 = _snapshot(repo, c2)
    tree3, blobs3 = _snapshot(repo, c3)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        ["--objects", "--in-commit-order", "--filter=blob:none", "--no-object-names", "HEAD"]
    ) == 0
    assert capsys.readouterr().out.splitlines() == [c3, tree3, c2, tree2, c1, tree1]

    assert run_rev_list_disk_usage(
        ["--objects", "--in-commit-order", "--filter=blob:none", "--count", "HEAD"]
    ) == 0
    assert capsys.readouterr().out.splitlines() == ["6"]

    for blob in (*blobs1, *blobs2, *blobs3):
        assert blob not in {c1, c2, c3, tree1, tree2, tree3}


def test_blob_none_reverse_changes_order_but_never_reintroduces_blobs(tmp_path, monkeypatch, capsys):
    repo, (c1, c2, c3) = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    tree1, blobs1 = _snapshot(repo, c1)
    tree2, blobs2 = _snapshot(repo, c2)
    tree3, blobs3 = _snapshot(repo, c3)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "--reverse",
            "--filter=blob:none",
            "--no-object-names",
            "HEAD",
        ]
    ) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines == [c1, tree1, c2, tree2, c3, tree3]
    assert not set((*blobs1, *blobs2, *blobs3)) & set(lines)


def test_blob_none_boundary_keeps_boundary_commit_and_tree(tmp_path, monkeypatch, capsys):
    repo, (_c1, c2, c3) = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    tree2, _ = _snapshot(repo, c2)
    tree3, _ = _snapshot(repo, c3)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "--boundary",
            "--max-count=1",
            "--filter=blob:none",
            "--no-object-names",
            "HEAD",
        ]
    ) == 0
    assert capsys.readouterr().out.splitlines() == [c3, tree3, f"-{c2}", tree2]


def test_blob_none_objects_edge_keeps_edge_outside_filter(tmp_path, monkeypatch, capsys):
    repo, (c1, c2, c3) = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    tree2, _ = _snapshot(repo, c2)
    tree3, _ = _snapshot(repo, c3)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects-edge",
            "--in-commit-order",
            "--filter=blob:none",
            "--no-object-names",
            f"{c1}..{c3}",
        ]
    ) == 0
    assert capsys.readouterr().out.splitlines() == [f"-{c1}", c3, tree3, c2, tree2]


def test_blob_none_nul_uses_ordered_structured_records(tmp_path, monkeypatch, capsys):
    repo, (c1, c2, c3) = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    tree1, blobs1 = _snapshot(repo, c1)
    tree2, blobs2 = _snapshot(repo, c2)
    tree3, blobs3 = _snapshot(repo, c3)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        ["--objects", "--in-commit-order", "-z", "--filter=blob:none", "HEAD"]
    ) == 0
    out = capsys.readouterr().out
    assert out == (
        f"{c3}\0{tree3}\0path=\0"
        f"{c2}\0{tree2}\0path=\0"
        f"{c1}\0{tree1}\0path=\0"
    )
    assert not set((*blobs1, *blobs2, *blobs3)) & set(out.split("\0"))


def test_blob_none_filters_promised_blob_before_ordinary_missing_validation(
    tmp_path, monkeypatch, capsys
):
    repo, commit, tree, native_blob = _partial_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    before = read_promisor_state(repo.pygit_dir)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        ["--objects", "--in-commit-order", "--filter=blob:none", "--no-object-names", "HEAD"]
    ) == 0
    assert capsys.readouterr().out.splitlines() == [commit, tree]
    assert native_blob not in capsys.readouterr().out
    assert read_promisor_state(repo.pygit_dir) == before


def test_blob_none_print_info_does_not_report_filtered_promised_blob(tmp_path, monkeypatch, capsys):
    repo, commit, tree, native_blob = _partial_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    before = read_promisor_state(repo.pygit_dir)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "--filter=blob:none",
            "--missing=print-info",
            "--no-object-names",
            "HEAD",
        ]
    ) == 0
    out = capsys.readouterr().out
    assert out.splitlines() == [commit, tree]
    assert native_blob not in out
    assert "missing" not in out
    assert read_promisor_state(repo.pygit_dir) == before


def test_blob_none_accepts_filter_provided_objects_for_commit_roots(tmp_path, monkeypatch, capsys):
    repo, (_c1, _c2, c3) = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    tree3, _ = _snapshot(repo, c3)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "--filter=blob:none",
            "--filter-provided-objects",
            "--max-count=1",
            "--no-object-names",
            "HEAD",
        ]
    ) == 0
    assert capsys.readouterr().out.splitlines()[0:2] == [c3, tree3]


def test_in_commit_order_filter_provided_still_requires_filter(tmp_path, monkeypatch):
    repo, _commits = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)

    with pytest.raises(ValueError, match="requires --filter"):
        run_rev_list_disk_usage(
            ["--objects", "--in-commit-order", "--filter-provided-objects", "HEAD"]
        )
