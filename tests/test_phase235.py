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


def test_rev_list_objects_boundary_allow_promisor_is_metadata_only(
    tmp_path, monkeypatch, capsys
):
    repo, base, tip, native_blob = _partial_linear_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_object",
        lambda *args, **kwargs: pytest.fail("boundary allow-promisor must not fetch"),
    )
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_objects",
        lambda *args, **kwargs: pytest.fail("boundary allow-promisor must not batch-fetch"),
    )

    before = read_promisor_state(repo.pygit_dir)
    capsys.readouterr()
    assert run_rev_list_disk_usage(
        ["--objects", "--boundary", "--missing=allow-promisor", f"{base}..{tip}"]
    ) == 0
    after = read_promisor_state(repo.pygit_dir)

    lines = capsys.readouterr().out.splitlines()
    assert before == after
    assert lines == [tip, f"-{base}"]
    assert native_blob not in "\n".join(lines)
    assert all(len(line.lstrip("-").split(" ", 1)[0]) == 64 for line in lines)


def test_rev_list_objects_boundary_reverse_moves_boundary_with_commit_stream(
    tmp_path, monkeypatch, capsys
):
    repo, base, tip, _ = _partial_linear_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--boundary",
            "--reverse",
            "--missing=allow-promisor",
            f"{base}..{tip}",
        ]
    ) == 0

    assert capsys.readouterr().out.splitlines() == [f"-{base}", tip]


def test_rev_list_objects_boundary_count_includes_boundary_record(
    tmp_path, monkeypatch, capsys
):
    repo, base, tip, _ = _partial_linear_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--boundary",
            "--count",
            "--missing=allow-promisor",
            f"{base}..{tip}",
        ]
    ) == 0

    assert capsys.readouterr().out.splitlines() == ["2"]


def test_rev_list_objects_boundary_no_object_names_preserves_dash_framing(
    tmp_path, monkeypatch, capsys
):
    repo, base, tip, _ = _partial_linear_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--boundary",
            "--no-object-names",
            "--missing=allow-promisor",
            f"{base}..{tip}",
        ]
    ) == 0

    assert capsys.readouterr().out.splitlines() == [tip, f"-{base}"]


def test_rev_list_objects_boundary_still_rejects_objects_edge_combination():
    with pytest.raises(ValueError, match="--boundary with --objects-edge"):
        run_rev_list_disk_usage(
            [
                "--objects-edge",
                "--boundary",
                "--missing=allow-promisor",
                "HEAD",
            ]
        )
