from __future__ import annotations

import hashlib

import pytest

from pygit.fetch_importer import PromisorFilteredNativeImporter
from pygit.objects import CommitObject, TreeObject
from pygit.promisor import read_promisor_state
from pygit.remote import NativeObject
from pygit.repo import Repository
from pygit.rev_list_disk_usage_cli import run_rev_list_disk_usage
from pygit.rev_list_filter_omitted_cli import _partition_projected_nul_count


def _repo(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
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
    blob = _native_oid("blob", b"payload\n")
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
        lambda *args, **kwargs: pytest.fail("NUL count must not single-fetch"),
    )
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_objects",
        lambda *args, **kwargs: pytest.fail("NUL count must not batch-fetch"),
    )


def test_plain_nul_count_is_newline_integer_only(tmp_path, monkeypatch, capsys):
    repo, _c1, _c2 = _repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(["--objects", "-z", "--count", "HEAD"]) == 0
    assert capsys.readouterr().out == "6\n"


def test_nul_boundary_count_matches_present_inventory(tmp_path, monkeypatch, capsys):
    repo, _c1, _c2 = _repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        ["--objects", "-z", "--boundary", "--max-count=1", "--count", "HEAD"]
    ) == 0
    assert capsys.readouterr().out == "6\n"


def test_nul_count_print_info_emits_missing_record_before_count_without_fetch(
    tmp_path, monkeypatch, capsys
):
    repo, native_blob = _partial_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    before = read_promisor_state(repo.pygit_dir)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        ["--objects", "-z", "--missing=print-info", "--count", "HEAD"]
    ) == 0
    output = capsys.readouterr().out
    assert output == (
        f"{native_blob}\0missing=yes\0path=f.txt\0type=blob\0"
        "2\n"
    )
    assert read_promisor_state(repo.pygit_dir) == before


def test_blob_none_nul_count_is_filtered_newline_integer(tmp_path, monkeypatch, capsys):
    repo, _c1, _c2 = _repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        ["--objects", "-z", "--filter=blob:none", "--count", "HEAD"]
    ) == 0
    assert capsys.readouterr().out == "4\n"


def test_object_type_nul_count_respects_filter_provided_objects(
    tmp_path, monkeypatch, capsys
):
    repo, _c1, _c2 = _repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "-z",
            "--filter=object:type=tree",
            "--filter-provided-objects",
            "--count",
            "HEAD",
        ]
    ) == 0
    assert capsys.readouterr().out == "2\n"


def test_ordered_nul_count_suppresses_structured_present_records(
    tmp_path, monkeypatch, capsys
):
    repo, _c1, _c2 = _repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        ["--objects", "--in-commit-order", "-z", "--count", "HEAD"]
    ) == 0
    assert capsys.readouterr().out == "6\n"


def test_ordered_blob_none_omitted_nul_count_orders_omissions_before_count(
    tmp_path, monkeypatch, capsys
):
    repo, _c1, c2 = _repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _tree2, blobs2 = _snapshot(repo, c2)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "-z",
            "--filter=blob:none",
            "--filter-print-omitted",
            "--count",
            "HEAD",
        ]
    ) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[-1] == "4"
    assert set(line[1:] for line in lines[:-1]) == set(blobs2.values())
    assert all(line.startswith("~") for line in lines[:-1])


def test_ordered_blob_limit_omitted_nul_count_matches_native_framing(
    tmp_path, monkeypatch, capsys
):
    repo, _c1, c2 = _repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _tree2, blobs2 = _snapshot(repo, c2)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--in-commit-order",
            "-z",
            "--filter=blob:limit=8",
            "--filter-print-omitted",
            "--count",
            "HEAD",
        ]
    ) == 0
    assert capsys.readouterr().out == f"~{blobs2['large.bin']}\n5\n"


def test_nul_count_partitioner_preserves_newlines_inside_missing_paths():
    missing = "a" * 40
    projected = (
        f"{missing}\0missing=yes\0path=line\nbreak.txt\0type=blob\0"
        "2\n"
    )

    traversal, missing_records, count_line = _partition_projected_nul_count(projected)

    assert traversal == ()
    assert missing_records == (
        f"{missing}\0missing=yes\0path=line\nbreak.txt\0type=blob\0",
    )
    assert count_line == "2"
