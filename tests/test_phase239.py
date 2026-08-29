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


def _tree(repo: Repository, commit_sha: str) -> str:
    commit = repo.store.read(commit_sha)
    assert isinstance(commit, CommitObject)
    return commit.tree


def _missing(native_oid: str) -> str:
    return f"?{native_oid} path=f.txt type=blob"


def _disable_fetch(monkeypatch):
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_object",
        lambda *args, **kwargs: pytest.fail("print-info boundary must not single-fetch"),
    )
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_objects",
        lambda *args, **kwargs: pytest.fail("print-info boundary must not batch-fetch"),
    )


def test_print_info_max_count_includes_boundary_snapshot_missing_records(
    tmp_path, monkeypatch, capsys
):
    repo, (_c1, c2, c3), (b1, b2, b3) = _partial_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    before = read_promisor_state(repo.pygit_dir)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--boundary",
            "--max-count=1",
            "--missing=print-info",
            "HEAD",
        ]
    ) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines == [
        c3,
        f"-{c2}",
        f"{_tree(repo, c3)} ",
        _missing(b3),
        f"{_tree(repo, c2)} ",
        _missing(b2),
    ]
    assert _missing(b1) not in lines
    assert read_promisor_state(repo.pygit_dir) == before


def test_print_info_boundary_reverse_reverses_snapshot_root_order(
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
            "--reverse",
            "--max-count=1",
            "--missing=print-info",
            "HEAD",
        ]
    ) == 0

    assert capsys.readouterr().out.splitlines() == [
        f"-{c2}",
        c3,
        f"{_tree(repo, c2)} ",
        _missing(b2),
        f"{_tree(repo, c3)} ",
        _missing(b3),
    ]


def test_print_info_skip_plus_max_count_uses_visible_parent_boundary(
    tmp_path, monkeypatch, capsys
):
    repo, (c1, c2, c3), (b1, b2, b3) = _partial_three_commit_repo(tmp_path)
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
            "HEAD",
        ]
    ) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines == [
        c2,
        f"-{c1}",
        f"{_tree(repo, c2)} ",
        _missing(b2),
        f"{_tree(repo, c1)} ",
        _missing(b1),
    ]
    assert c3 not in "\n".join(lines)
    assert _missing(b3) not in lines


def test_print_info_explicit_exclusion_subtracts_boundary_snapshot_closure(
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
            f"{c2}..{c3}",
        ]
    ) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines == [c3, f"-{c2}", f"{_tree(repo, c3)} ", _missing(b3)]
    assert _tree(repo, c2) not in "\n".join(lines)
    assert _missing(b2) not in lines


def test_print_info_boundary_no_object_names_keeps_missing_metadata(
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
            "--no-object-names",
            "--max-count=1",
            "--missing=print-info",
            "HEAD",
        ]
    ) == 0

    assert capsys.readouterr().out.splitlines() == [
        c3,
        f"-{c2}",
        _tree(repo, c3),
        _missing(b3),
        _tree(repo, c2),
        _missing(b2),
    ]
