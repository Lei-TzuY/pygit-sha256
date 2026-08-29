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


def _commit_data(
    tree_oid: str,
    *,
    message: str,
    timestamp: int,
    parent: str | None = None,
) -> bytes:
    parent_line = f"parent {parent}\n" if parent is not None else ""
    return (
        f"tree {tree_oid}\n"
        f"{parent_line}"
        f"author Test <test@example.com> {timestamp} +0000\n"
        f"committer Test <test@example.com> {timestamp} +0000\n"
        f"\n{message}"
    ).encode()


def _partial_three_commit_repo(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")

    native_objects = {}
    commit_oids = []
    blob_oids = []
    parent = None
    for index, payload in enumerate((b"one\n", b"two\n", b"three\n"), start=1):
        blob_oid = _native_oid("blob", payload)
        tree_data = _tree_data(blob_oid)
        tree_oid = _native_oid("tree", tree_data)
        commit_data = _commit_data(
            tree_oid,
            message=f"c{index}",
            timestamp=index,
            parent=parent,
        )
        commit_oid = _native_oid("commit", commit_data)
        native_objects[tree_oid] = NativeObject("tree", tree_data, tree_oid)
        native_objects[commit_oid] = NativeObject("commit", commit_data, commit_oid)
        blob_oids.append(blob_oid)
        commit_oids.append(commit_oid)
        parent = commit_oid

    importer = PromisorFilteredNativeImporter(
        repo.store,
        native_objects,
        remote="origin",
        filter_spec="blob:none",
    )
    local = [importer.import_oid(oid) for oid in commit_oids]
    repo.refs.set_branch("main", local[-1], message="test: partial tip")
    repo.refs.set_head_symbolic("main", message="test: partial tip")
    return repo, tuple(local), tuple(blob_oids)


def _missing(native_oid: str) -> str:
    return f"?{native_oid} path=f.txt type=blob"


def _disable_fetch(monkeypatch):
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_object",
        lambda *args, **kwargs: pytest.fail("print-info count must not single-fetch"),
    )
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_objects",
        lambda *args, **kwargs: pytest.fail("print-info count must not batch-fetch"),
    )


def test_print_info_count_emits_missing_then_counts_present_only(
    tmp_path, monkeypatch, capsys
):
    repo, (_c1, _c2, _c3), (_b1, _b2, b3) = _partial_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    before = read_promisor_state(repo.pygit_dir)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--max-count=1",
            "--missing=print-info",
            "--count",
            "HEAD",
        ]
    ) == 0

    assert capsys.readouterr().out.splitlines() == [_missing(b3), "2"]
    assert read_promisor_state(repo.pygit_dir) == before


def test_print_info_boundary_count_includes_boundary_present_objects_not_missing(
    tmp_path, monkeypatch, capsys
):
    repo, (_c1, _c2, _c3), (_b1, b2, b3) = _partial_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--boundary",
            "--max-count=1",
            "--missing=print-info",
            "--count",
            "HEAD",
        ]
    ) == 0

    # Selected commit + boundary commit + two present trees = 4. Promised
    # blobs remain visible as ? records but are not included in the count.
    assert capsys.readouterr().out.splitlines() == [
        _missing(b3),
        _missing(b2),
        "4",
    ]


def test_print_info_boundary_count_honors_skip_and_visible_parent_boundary(
    tmp_path, monkeypatch, capsys
):
    repo, (_c1, _c2, _c3), (b1, b2, _b3) = _partial_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--boundary",
            "--skip=1",
            "--max-count=1",
            "--missing=print-info",
            "--count",
            "HEAD",
        ]
    ) == 0

    assert capsys.readouterr().out.splitlines() == [
        _missing(b2),
        _missing(b1),
        "4",
    ]


def test_print_info_boundary_count_keeps_explicit_exclusion_authoritative(
    tmp_path, monkeypatch, capsys
):
    repo, (_c1, c2, c3), (_b1, b2, b3) = _partial_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--boundary",
            "--missing=print-info",
            "--count",
            f"{c2}..{c3}",
        ]
    ) == 0

    # c3 + boundary c2 + c3 tree. c2's explicitly excluded tree/blob closure
    # stays subtracted; only c3's missing blob is reported.
    lines = capsys.readouterr().out.splitlines()
    assert lines == [_missing(b3), "3"]
    assert _missing(b2) not in lines


def test_print_info_count_no_object_names_keeps_missing_metadata(
    tmp_path, monkeypatch, capsys
):
    repo, (_c1, _c2, _c3), (_b1, _b2, b3) = _partial_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--no-object-names",
            "--max-count=1",
            "--missing=print-info",
            "--count",
            "HEAD",
        ]
    ) == 0

    assert capsys.readouterr().out.splitlines() == [_missing(b3), "2"]


def test_print_info_count_ordinary_repo_emits_only_present_count(
    tmp_path, monkeypatch, capsys
):
    repo = Repository.init(str(tmp_path / "ordinary"))
    (repo.worktree / "a.txt").write_text("alpha\n", encoding="utf-8")
    repo.add(["a.txt"])
    repo.commit("ordinary", author_name="Test", author_email="test@example.com")
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        ["--objects", "--missing=print-info", "--count", "HEAD"]
    ) == 0

    assert capsys.readouterr().out.splitlines() == ["3"]
