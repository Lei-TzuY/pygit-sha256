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


def _disable_fetch(monkeypatch):
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_object",
        lambda *args, **kwargs: pytest.fail("ordered omissions must not single-fetch"),
    )
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_objects",
        lambda *args, **kwargs: pytest.fail("ordered omissions must not batch-fetch"),
    )


def _omitted(lines: list[str]) -> set[str]:
    return {line[1:] for line in lines if line.startswith("~")}


def test_in_commit_order_blob_none_prints_omissions_after_ordered_traversal(
    tmp_path, monkeypatch, capsys
):
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
            "--filter=blob:none",
            "--filter-print-omitted",
            "--no-object-names",
            "HEAD",
        ]
    ) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[:6] == [c3, tree3, c2, tree2, c1, tree1]
    assert all(line.startswith("~") for line in lines[6:])
    assert _omitted(lines) == set(blobs3)
    assert all(len(oid) == 64 for oid in _omitted(lines))
    assert blobs1[0] in _omitted(lines)
    assert blobs2[1] in _omitted(lines)


def test_in_commit_order_blob_none_reverse_keeps_omissions_after_traversal(
    tmp_path, monkeypatch, capsys
):
    repo, (c1, c2, c3) = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    tree1, _ = _snapshot(repo, c1)
    tree2, _ = _snapshot(repo, c2)
    tree3, blobs3 = _snapshot(repo, c3)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "--reverse",
            "--filter=blob:none",
            "--filter-print-omitted",
            "--no-object-names",
            "HEAD",
        ]
    ) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[:6] == [c1, tree1, c2, tree2, c3, tree3]
    assert all(line.startswith("~") for line in lines[6:])
    assert _omitted(lines) == set(blobs3)


def test_in_commit_order_blob_none_count_places_omissions_before_count(
    tmp_path, monkeypatch, capsys
):
    repo, (_c1, _c2, c3) = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _tree3, blobs3 = _snapshot(repo, c3)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "--filter=blob:none",
            "--filter-print-omitted",
            "--count",
            "HEAD",
        ]
    ) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[-1] == "6"
    assert all(line.startswith("~") for line in lines[:-1])
    assert _omitted(lines[:-1]) == set(blobs3)


def test_in_commit_order_blob_none_boundary_finishes_before_omissions(
    tmp_path, monkeypatch, capsys
):
    repo, (_c1, c2, c3) = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    tree2, _ = _snapshot(repo, c2)
    tree3, blobs3 = _snapshot(repo, c3)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "--boundary",
            "--max-count=1",
            "--filter=blob:none",
            "--filter-print-omitted",
            "--no-object-names",
            "HEAD",
        ]
    ) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[:4] == [c3, tree3, f"-{c2}", tree2]
    assert all(line.startswith("~") for line in lines[4:])
    assert _omitted(lines) == set(blobs3)


def test_in_commit_order_objects_edge_excludes_base_only_blob_from_omissions(
    tmp_path, monkeypatch, capsys
):
    repo, (c1, c2, c3) = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    tree2, blobs2 = _snapshot(repo, c2)
    tree3, blobs3 = _snapshot(repo, c3)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects-edge",
            "--in-commit-order",
            "--filter=blob:none",
            "--filter-print-omitted",
            "--no-object-names",
            f"{c1}..{c3}",
        ]
    ) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[:5] == [f"-{c1}", c3, tree3, c2, tree2]
    assert _omitted(lines[5:]) == {blobs3[1], blobs3[2]}
    assert blobs2[0] not in _omitted(lines[5:])


def test_in_commit_order_blob_none_nul_keeps_native_mixed_omission_framing(
    tmp_path, monkeypatch, capsys
):
    repo, (c1, c2, c3) = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    tree1, _ = _snapshot(repo, c1)
    tree2, _ = _snapshot(repo, c2)
    tree3, blobs3 = _snapshot(repo, c3)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "-z",
            "--filter=blob:none",
            "--filter-print-omitted",
            "--no-object-names",
            "HEAD",
        ]
    ) == 0

    output = capsys.readouterr().out
    first_omitted = output.index("~")
    traversal = output[:first_omitted]
    omission_text = output[first_omitted:]
    fields = [field for field in traversal.split("\0") if field]
    assert fields == [c3, tree3, c2, tree2, c1, tree1]
    omitted_lines = omission_text.splitlines()
    assert all(line.startswith("~") for line in omitted_lines)
    assert _omitted(omitted_lines) == set(blobs3)


def test_in_commit_order_filter_omitted_rejects_unresolved_promised_blob_without_fetch(
    tmp_path, monkeypatch, capsys
):
    repo, _base, _tip, a_blob, _b_blob = _partial_two_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    before = read_promisor_state(repo.pygit_dir)
    capsys.readouterr()

    with pytest.raises(ValueError, match="cannot expose unresolved promisor object"):
        run_rev_list_disk_usage(
            [
                "--objects",
                "--in-commit-order",
                "--filter=blob:none",
                "--filter-print-omitted",
                "--missing=allow-promisor",
                "HEAD",
            ]
        )

    assert capsys.readouterr().out == ""
    assert read_promisor_state(repo.pygit_dir) == before
    assert len(a_blob) == 40


def test_in_commit_order_filter_omitted_rejects_unsupported_filter_family(
    tmp_path, monkeypatch
):
    repo, _commits = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)

    with pytest.raises(ValueError, match="currently supports only --filter=blob:none"):
        run_rev_list_disk_usage(
            [
                "--objects",
                "--in-commit-order",
                "--filter=object:type=tree",
                "--filter-print-omitted",
                "HEAD",
            ]
        )
