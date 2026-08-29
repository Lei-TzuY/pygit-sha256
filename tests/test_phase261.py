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


def _snapshot(repo: Repository, commit_sha: str) -> tuple[str, tuple[str, ...]]:
    commit = repo.store.read(commit_sha)
    assert isinstance(commit, CommitObject)
    tree = repo.store.read(commit.tree)
    assert isinstance(tree, TreeObject)
    blobs = tuple(entry.sha.lower() for entry in sorted(tree.entries, key=lambda entry: entry.name))
    return commit.tree.lower(), blobs


def _native_oid(type_name: str, data: bytes) -> str:
    return hashlib.sha1(f"{type_name} {len(data)}\0".encode() + data).hexdigest()


def _tree_data(entries: tuple[tuple[str, str], ...]) -> bytes:
    return b"".join(
        f"100644 {name}\0".encode() + bytes.fromhex(oid)
        for name, oid in entries
    )


def _commit_data(tree_oid: str, *, parent: str | None = None, timestamp: int) -> bytes:
    parent_line = f"parent {parent}\n" if parent else ""
    return (
        f"tree {tree_oid}\n"
        f"{parent_line}"
        f"author Test <test@example.com> {timestamp} +0000\n"
        f"committer Test <test@example.com> {timestamp} +0000\n"
        "\nmsg\n"
    ).encode()


def _partial_two_commit_repo(tmp_path):
    repo = Repository.init(str(tmp_path / "partial"))
    repo.add_remote("origin", "https://example.test/repo.git")

    a_blob = _native_oid("blob", b"a\n")
    b_blob = _native_oid("blob", b"b\n")
    base_tree_data = _tree_data((("a.txt", a_blob),))
    base_tree = _native_oid("tree", base_tree_data)
    base_commit_data = _commit_data(base_tree, timestamp=1)
    base_commit = _native_oid("commit", base_commit_data)

    tip_tree_data = _tree_data((("a.txt", a_blob), ("b.txt", b_blob)))
    tip_tree = _native_oid("tree", tip_tree_data)
    tip_commit_data = _commit_data(tip_tree, parent=base_commit, timestamp=2)
    tip_commit = _native_oid("commit", tip_commit_data)

    importer = PromisorFilteredNativeImporter(
        repo.store,
        {
            base_tree: NativeObject("tree", base_tree_data, base_tree),
            base_commit: NativeObject("commit", base_commit_data, base_commit),
            tip_tree: NativeObject("tree", tip_tree_data, tip_tree),
            tip_commit: NativeObject("commit", tip_commit_data, tip_commit),
        },
        remote="origin",
        filter_spec="blob:none",
    )
    local_base = importer.import_oid(base_commit)
    local_tip = importer.import_oid(tip_commit)
    repo.refs.set_branch("main", local_tip, message="test: partial tip")
    repo.refs.set_head_symbolic("main", message="test: partial tip")
    return repo, local_base, local_tip, a_blob, b_blob


def _tree_oid(repo: Repository, commit_sha: str) -> str:
    commit = repo.store.read(commit_sha)
    assert isinstance(commit, CommitObject)
    return commit.tree.lower()


def _disable_fetch(monkeypatch):
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_object",
        lambda *args, **kwargs: pytest.fail("objects-edge in-commit-order must not single-fetch"),
    )
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_objects",
        lambda *args, **kwargs: pytest.fail("objects-edge in-commit-order must not batch-fetch"),
    )


def test_in_commit_order_objects_edge_emits_edge_before_interleaved_snapshots(
    tmp_path, monkeypatch, capsys
):
    repo, (c1, c2, c3) = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    tree2, _blobs2 = _snapshot(repo, c2)
    tree3, blobs3 = _snapshot(repo, c3)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects-edge",
            "--in-commit-order",
            "--no-object-names",
            f"{c1}..{c3}",
        ]
    ) == 0

    assert capsys.readouterr().out.splitlines() == [
        f"-{c1}",
        c3,
        tree3,
        blobs3[1],
        blobs3[2],
        c2,
        tree2,
    ]


def test_in_commit_order_objects_edge_reverse_keeps_edge_first(tmp_path, monkeypatch, capsys):
    repo, (c1, c2, c3) = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    tree2, blobs2 = _snapshot(repo, c2)
    tree3, blobs3 = _snapshot(repo, c3)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects-edge",
            "--in-commit-order",
            "--reverse",
            "--no-object-names",
            f"{c1}..{c3}",
        ]
    ) == 0

    assert capsys.readouterr().out.splitlines() == [
        f"-{c1}",
        c2,
        tree2,
        blobs2[1],
        c3,
        tree3,
        blobs3[2],
    ]


def test_in_commit_order_objects_edge_count_prints_edge_but_does_not_count_it(
    tmp_path, monkeypatch, capsys
):
    repo, (c1, _c2, c3) = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        ["--objects-edge", "--in-commit-order", "--count", f"{c1}..{c3}"]
    ) == 0

    assert capsys.readouterr().out.splitlines() == [f"-{c1}", "6"]


def test_in_commit_order_objects_edge_print_info_is_metadata_only(
    tmp_path, monkeypatch, capsys
):
    repo, base, tip, _a_blob, b_blob = _partial_two_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    before = read_promisor_state(repo.pygit_dir)
    tip_tree = _tree_oid(repo, tip)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects-edge",
            "--in-commit-order",
            "--missing=print-info",
            "--no-object-names",
            f"{base}..{tip}",
        ]
    ) == 0

    assert capsys.readouterr().out.splitlines() == [
        f"-{base}",
        tip,
        tip_tree,
        f"?{b_blob} path=b.txt type=blob",
    ]
    assert read_promisor_state(repo.pygit_dir) == before


def test_in_commit_order_objects_edge_ordinary_partial_fails_before_edge_output(
    tmp_path, monkeypatch, capsys
):
    repo, base, tip, _a_blob, _b_blob = _partial_two_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    before = read_promisor_state(repo.pygit_dir)
    capsys.readouterr()

    with pytest.raises(RuntimeError, match="use --missing=allow-promisor"):
        run_rev_list_disk_usage(
            ["--objects-edge", "--in-commit-order", f"{base}..{tip}"]
        )

    assert capsys.readouterr().out == ""
    assert read_promisor_state(repo.pygit_dir) == before


def test_in_commit_order_objects_edge_nul_remains_explicitly_deferred(
    tmp_path, monkeypatch, capsys
):
    repo, (_c1, _c2, _c3) = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    with pytest.raises(ValueError, match="with -z"):
        run_rev_list_disk_usage(
            ["--objects-edge", "--in-commit-order", "-z", "HEAD"]
        )
