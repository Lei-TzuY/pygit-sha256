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
    blobs = tuple(
        entry.sha.lower() for entry in sorted(tree.entries, key=lambda item: item.name)
    )
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
        lambda *args, **kwargs: pytest.fail("ordered object:type must not single-fetch"),
    )
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_objects",
        lambda *args, **kwargs: pytest.fail("ordered object:type must not batch-fetch"),
    )


def test_object_type_commit_preserves_ordered_commit_stream(tmp_path, monkeypatch, capsys):
    repo, (c1, c2, c3) = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "--filter=object:type=commit",
            "--no-object-names",
            "HEAD",
        ]
    ) == 0
    assert capsys.readouterr().out.splitlines() == [c3, c2, c1]


def test_object_type_tree_preserves_provided_root_exemption(tmp_path, monkeypatch, capsys):
    repo, (c1, c2, c3) = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    tree1, _ = _snapshot(repo, c1)
    tree2, _ = _snapshot(repo, c2)
    tree3, _ = _snapshot(repo, c3)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "--filter=object:type=tree",
            "--no-object-names",
            "HEAD",
        ]
    ) == 0
    assert capsys.readouterr().out.splitlines() == [c3, tree3, tree2, tree1]

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "--filter=object:type=tree",
            "--filter-provided-objects",
            "--no-object-names",
            "HEAD",
        ]
    ) == 0
    assert capsys.readouterr().out.splitlines() == [tree3, tree2, tree1]


def test_object_type_blob_keeps_first_snapshot_blobs_and_root(tmp_path, monkeypatch, capsys):
    repo, (_c1, _c2, c3) = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _tree3, blobs3 = _snapshot(repo, c3)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "--filter=object:type=blob",
            "--no-object-names",
            "HEAD",
        ]
    ) == 0
    assert capsys.readouterr().out.splitlines() == [c3, *blobs3]

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "--filter=object:type=blob",
            "--filter-provided-objects",
            "--no-object-names",
            "HEAD",
        ]
    ) == 0
    assert capsys.readouterr().out.splitlines() == list(blobs3)


def test_object_type_count_uses_filtered_present_inventory(tmp_path, monkeypatch, capsys):
    repo, (c1, c2, c3) = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        ["--objects", "--in-commit-order", "--filter=object:type=tree", "--count", "HEAD"]
    ) == 0
    assert capsys.readouterr().out.splitlines() == ["4"]

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "--filter=object:type=tree",
            "--filter-provided-objects",
            "--count",
            "HEAD",
        ]
    ) == 0
    assert capsys.readouterr().out.splitlines() == ["3"]


def test_object_type_boundary_filters_boundary_frame_by_type(tmp_path, monkeypatch, capsys):
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
            "--filter=object:type=tree",
            "--no-object-names",
            "HEAD",
        ]
    ) == 0
    assert capsys.readouterr().out.splitlines() == [c3, tree3, tree2]

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "--boundary",
            "--max-count=1",
            "--filter=object:type=commit",
            "--no-object-names",
            "HEAD",
        ]
    ) == 0
    assert capsys.readouterr().out.splitlines() == [c3, f"-{c2}"]


def test_object_type_objects_edge_remains_independent_presentation(tmp_path, monkeypatch, capsys):
    repo, (c1, c2, c3) = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    tree2, _ = _snapshot(repo, c2)
    tree3, _ = _snapshot(repo, c3)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects-edge",
            "--in-commit-order",
            "--filter=object:type=tree",
            "--no-object-names",
            f"{c1}..{c3}",
        ]
    ) == 0
    assert capsys.readouterr().out.splitlines() == [f"-{c1}", c3, tree3, tree2]


def test_object_type_nul_and_empty_omitted_set(tmp_path, monkeypatch, capsys):
    repo, (c1, c2, c3) = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    tree1, _ = _snapshot(repo, c1)
    tree2, _ = _snapshot(repo, c2)
    tree3, _ = _snapshot(repo, c3)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "-z",
            "--filter=object:type=tree",
            "--filter-print-omitted",
            "HEAD",
        ]
    ) == 0
    out = capsys.readouterr().out
    assert out == (
        f"{c3}\0{tree3}\0path=\0"
        f"{tree2}\0path=\0"
        f"{tree1}\0path=\0"
    )
    assert "~" not in out


def test_object_type_partial_clone_filters_nonmatching_promise_before_validation(
    tmp_path, monkeypatch, capsys
):
    repo, commit, tree, native_blob = _partial_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    before = read_promisor_state(repo.pygit_dir)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "--filter=object:type=tree",
            "--no-object-names",
            "HEAD",
        ]
    ) == 0
    out = capsys.readouterr().out
    assert out.splitlines() == [commit, tree]
    assert native_blob not in out
    assert read_promisor_state(repo.pygit_dir) == before


def test_object_type_partial_clone_matching_promise_uses_missing_channel(
    tmp_path, monkeypatch, capsys
):
    repo, commit, _tree, native_blob = _partial_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    before = read_promisor_state(repo.pygit_dir)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "--filter=object:type=blob",
            "--missing=print-info",
            "--no-object-names",
            "HEAD",
        ]
    ) == 0
    assert capsys.readouterr().out.splitlines() == [
        commit,
        f"?{native_blob} path=f.txt type=blob",
    ]
    assert read_promisor_state(repo.pygit_dir) == before


def test_object_type_rejects_tag_and_still_defers_disk_usage(tmp_path, monkeypatch):
    repo, _commits = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)

    with pytest.raises(ValueError, match="object:type=tag"):
        run_rev_list_disk_usage(
            ["--objects", "--in-commit-order", "--filter=object:type=tag", "HEAD"]
        )
    with pytest.raises(ValueError, match="with --disk-usage"):
        run_rev_list_disk_usage(
            [
                "--objects",
                "--in-commit-order",
                "--filter=object:type=tree",
                "--disk-usage",
                "HEAD",
            ]
        )
