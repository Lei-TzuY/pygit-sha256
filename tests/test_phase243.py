from __future__ import annotations

import hashlib

import pytest

from pygit.fetch_importer import PromisorFilteredNativeImporter
from pygit.objects import CommitObject
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


def _partial_range_repo(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")

    base_blob = _native_oid("blob", b"base\n")
    base_tree_data = _tree_data(base_blob)
    base_tree = _native_oid("tree", base_tree_data)
    base_data = _commit_data(base_tree, message="base")
    base_commit = _native_oid("commit", base_data)

    tip_blob = _native_oid("blob", b"tip\n")
    tip_tree_data = _tree_data(tip_blob)
    tip_tree = _native_oid("tree", tip_tree_data)
    tip_data = _commit_data(tip_tree, message="tip", parent=base_commit)
    tip_commit = _native_oid("commit", tip_data)

    importer = PromisorFilteredNativeImporter(
        repo.store,
        {
            base_tree: NativeObject("tree", base_tree_data, base_tree),
            base_commit: NativeObject("commit", base_data, base_commit),
            tip_tree: NativeObject("tree", tip_tree_data, tip_tree),
            tip_commit: NativeObject("commit", tip_data, tip_commit),
        },
        remote="origin",
        filter_spec="blob:none",
    )
    local_base = importer.import_oid(base_commit)
    local_tip = importer.import_oid(tip_commit)
    repo.refs.set_branch("main", local_tip, message="test: partial tip")
    repo.refs.set_head_symbolic("main", message="test: partial tip")
    return repo, local_base, local_tip, base_blob, tip_blob


def _tree(repo: Repository, commit_sha: str) -> str:
    commit = repo.store.read(commit_sha)
    assert isinstance(commit, CommitObject)
    return commit.tree


def _disable_fetch(monkeypatch):
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_object",
        lambda *args, **kwargs: pytest.fail("edge-boundary traversal must not single-fetch"),
    )
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_objects",
        lambda *args, **kwargs: pytest.fail("edge-boundary traversal must not batch-fetch"),
    )


@pytest.mark.parametrize("missing_mode", ["print", "print-info"])
def test_missing_objects_edge_boundary_deduplicates_explicit_edge(
    tmp_path, monkeypatch, capsys, missing_mode
):
    repo, base, tip, base_blob, tip_blob = _partial_range_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    before = read_promisor_state(repo.pygit_dir)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects-edge",
            "--boundary",
            f"--missing={missing_mode}",
            f"{base}..{tip}",
        ]
    ) == 0

    missing = f"?{tip_blob}"
    if missing_mode == "print-info":
        missing += " path=f.txt type=blob"
    lines = capsys.readouterr().out.splitlines()
    assert lines == [f"-{base}", tip, f"{_tree(repo, tip)} ", missing]
    assert lines.count(f"-{base}") == 1
    assert base_blob not in "\n".join(lines)
    assert _tree(repo, base) not in "\n".join(lines)
    assert read_promisor_state(repo.pygit_dir) == before


@pytest.mark.parametrize("missing_mode", ["print", "print-info"])
def test_missing_objects_edge_boundary_count_does_not_count_duplicate_edge(
    tmp_path, monkeypatch, capsys, missing_mode
):
    repo, base, tip, _base_blob, tip_blob = _partial_range_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects-edge",
            "--boundary",
            f"--missing={missing_mode}",
            "--count",
            f"{base}..{tip}",
        ]
    ) == 0

    missing = f"?{tip_blob}"
    if missing_mode == "print-info":
        missing += " path=f.txt type=blob"
    # Native Git advertises the excluded edge but does not include it in the
    # final object count. The missing promise is likewise reported but not
    # counted; only the selected commit and selected tree remain present.
    assert capsys.readouterr().out.splitlines() == [f"-{base}", missing, "2"]


def test_missing_objects_edge_boundary_no_overlap_keeps_boundary_stream(
    tmp_path, monkeypatch, capsys
):
    repo, _base, _tip, _base_blob, tip_blob = _partial_range_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    capsys.readouterr()

    # HEAD has no explicit negative revision, hence no object edge. Boundary
    # mode is still accepted and the ordinary print-info traversal remains
    # authoritative.
    assert run_rev_list_disk_usage(
        ["--objects-edge", "--boundary", "--missing=print-info", "HEAD"]
    ) == 0

    lines = capsys.readouterr().out.splitlines()
    assert not any(line.startswith("-") for line in lines)
    assert any(line == f"?{tip_blob} path=f.txt type=blob" for line in lines)
