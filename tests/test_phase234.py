from __future__ import annotations

import hashlib

import pytest

from pygit.fetch_importer import PromisorFilteredNativeImporter
from pygit.promisor import read_promisor_state
from pygit.remote import NativeObject
from pygit.repo import Repository
from pygit.rev_list_disk_usage_cli import run_rev_list_disk_usage


def _native_oid(type_name: str, data: bytes) -> str:
    return hashlib.sha1(f"{type_name} {len(data)}\0".encode() + data).hexdigest()


def _tree_data(blob_oid: str) -> bytes:
    return b"100644 f.txt\x00" + bytes.fromhex(blob_oid)


def _commit_data(tree_oid: str, *, message: str, parent: str | None = None) -> bytes:
    parent_line = f"parent {parent}\n" if parent is not None else ""
    return (
        f"tree {tree_oid}\n"
        f"{parent_line}"
        "author Test <test@example.com> 1 +0000\n"
        "committer Test <test@example.com> 1 +0000\n"
        f"\n{message}"
    ).encode()


def _partial_linear_repo(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")

    blob_data = b"promised\n"
    blob_oid = _native_oid("blob", blob_data)
    tree_data = _tree_data(blob_oid)
    tree_oid = _native_oid("tree", tree_data)
    base_data = _commit_data(tree_oid, message="base")
    base_oid = _native_oid("commit", base_data)
    tip_data = _commit_data(tree_oid, message="tip", parent=base_oid)
    tip_oid = _native_oid("commit", tip_data)

    importer = PromisorFilteredNativeImporter(
        repo.store,
        {
            tree_oid: NativeObject("tree", tree_data, tree_oid),
            base_oid: NativeObject("commit", base_data, base_oid),
            tip_oid: NativeObject("commit", tip_data, tip_oid),
        },
        remote="origin",
        filter_spec="blob:none",
    )
    local_base = importer.import_oid(base_oid)
    local_tip = importer.import_oid(tip_oid)
    repo.refs.set_branch("main", local_tip, message="test: partial tip")
    repo.refs.set_head_symbolic("main", message="test: partial tip")
    return repo, local_base, local_tip, blob_oid


def test_rev_list_objects_edge_allow_promisor_is_metadata_only(
    tmp_path, monkeypatch, capsys
):
    repo, base, tip, native_blob = _partial_linear_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_object",
        lambda *args, **kwargs: pytest.fail("objects-edge allow-promisor must not fetch"),
    )
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_objects",
        lambda *args, **kwargs: pytest.fail("objects-edge allow-promisor must not batch-fetch"),
    )

    before = read_promisor_state(repo.pygit_dir)
    capsys.readouterr()
    assert run_rev_list_disk_usage(
        ["--objects-edge", "--missing=allow-promisor", f"{base}..{tip}"]
    ) == 0
    after = read_promisor_state(repo.pygit_dir)

    lines = capsys.readouterr().out.splitlines()
    assert before == after
    assert lines == [f"-{base}", tip]
    assert native_blob not in "\n".join(lines)
    assert all(len(line.lstrip("-").split(" ", 1)[0]) == 64 for line in lines)


def test_rev_list_objects_edge_count_prints_edge_then_present_count(
    tmp_path, monkeypatch, capsys
):
    repo, base, tip, _ = _partial_linear_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects-edge",
            "--missing=allow-promisor",
            "--count",
            f"{base}..{tip}",
        ]
    ) == 0

    # Native Git frames --objects-edge --count as edge lines followed by a
    # count that excludes those edge commits. The shared tree is excluded by
    # the base closure, so only the selected tip remains present here.
    assert capsys.readouterr().out.splitlines() == [f"-{base}", "1"]


def test_rev_list_objects_edge_boundary_ignores_max_count_for_edge_discovery(
    tmp_path, monkeypatch, capsys
):
    repo = Repository.init(str(tmp_path / "ordinary"))
    (repo.worktree / "f.txt").write_text("one\n", encoding="utf-8")
    repo.add(["f.txt"])
    base = repo.commit("base", author_name="Test", author_email="test@example.com")
    (repo.worktree / "f.txt").write_text("two\n", encoding="utf-8")
    repo.add(["f.txt"])
    repo.commit("middle", author_name="Test", author_email="test@example.com")
    (repo.worktree / "f.txt").write_text("three\n", encoding="utf-8")
    repo.add(["f.txt"])
    tip = repo.commit("tip", author_name="Test", author_email="test@example.com")
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects-edge",
            "--missing=allow-promisor",
            "--max-count=1",
            f"{base}..{tip}",
        ]
    ) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == f"-{base}"
    assert lines[1] == tip
    assert all(len(line.lstrip("-").split(" ", 1)[0]) == 64 for line in lines)


def test_rev_list_allow_promisor_rejects_multiple_object_modes(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "ordinary"))
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)

    with pytest.raises(ValueError, match="exactly one"):
        run_rev_list_disk_usage(
            ["--objects", "--objects-edge", "--missing=allow-promisor", "HEAD"]
        )
