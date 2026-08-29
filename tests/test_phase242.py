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
        lambda *args, **kwargs: pytest.fail("missing objects-edge must not single-fetch"),
    )
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_objects",
        lambda *args, **kwargs: pytest.fail("missing objects-edge must not batch-fetch"),
    )


def test_print_info_objects_edge_keeps_edge_and_missing_channels_separate(
    tmp_path, monkeypatch, capsys
):
    repo, base, tip, base_blob, tip_blob = _partial_range_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    before = read_promisor_state(repo.pygit_dir)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        ["--objects-edge", "--missing=print-info", f"{base}..{tip}"]
    ) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines == [
        f"-{base}",
        tip,
        f"{_tree(repo, tip)} ",
        f"?{tip_blob} path=f.txt type=blob",
    ]
    joined = "\n".join(lines)
    assert base_blob not in joined
    assert _tree(repo, base) not in joined
    assert read_promisor_state(repo.pygit_dir) == before


def test_plain_print_objects_edge_is_projection_of_print_info(
    tmp_path, monkeypatch, capsys
):
    repo, base, tip, base_blob, tip_blob = _partial_range_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        ["--objects-edge", "--missing=print", f"{base}..{tip}"]
    ) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines == [f"-{base}", tip, f"{_tree(repo, tip)} ", f"?{tip_blob}"]
    assert all("path=" not in line and "type=" not in line for line in lines if line.startswith("?"))
    assert base_blob not in "\n".join(lines)


@pytest.mark.parametrize("missing_mode", ["print", "print-info"])
def test_missing_objects_edge_count_excludes_edges_and_promises(
    tmp_path, monkeypatch, capsys, missing_mode
):
    repo, base, tip, _base_blob, tip_blob = _partial_range_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects-edge",
            f"--missing={missing_mode}",
            "--count",
            f"{base}..{tip}",
        ]
    ) == 0

    missing = f"?{tip_blob}"
    if missing_mode == "print-info":
        missing += " path=f.txt type=blob"
    # The excluded edge is advertised but not counted. The promised blob is
    # reported but also not counted. Only the selected tip commit and its tree
    # contribute to the final present-object count.
    assert capsys.readouterr().out.splitlines() == [f"-{base}", missing, "2"]


@pytest.mark.parametrize("missing_mode", ["print", "print-info"])
def test_missing_objects_edge_boundary_combination_remains_deferred(
    tmp_path, monkeypatch, missing_mode
):
    repo, _base, _tip, _base_blob, _tip_blob = _partial_range_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)

    with pytest.raises(ValueError, match="--boundary with --objects-edge"):
        run_rev_list_disk_usage(
            [
                "--objects-edge",
                "--boundary",
                f"--missing={missing_mode}",
                "HEAD",
            ]
        )
