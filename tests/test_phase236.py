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
    parent = None
    blob_oids = []
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


def _install_no_fetch(monkeypatch):
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_object",
        lambda *args, **kwargs: pytest.fail("allow-promisor boundary must not fetch"),
    )
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_objects",
        lambda *args, **kwargs: pytest.fail("allow-promisor boundary must not batch-fetch"),
    )


def test_max_count_adds_boundary_snapshot_closure_without_fetch(
    tmp_path, monkeypatch, capsys
):
    repo, (c1, c2, c3), promised = _partial_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _install_no_fetch(monkeypatch)
    before = read_promisor_state(repo.pygit_dir)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--boundary",
            "--max-count=1",
            "--missing=allow-promisor",
            "HEAD",
        ]
    ) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines == [c3, f"-{c2}", f"{_tree(repo, c3)} ", f"{_tree(repo, c2)} "]
    assert read_promisor_state(repo.pygit_dir) == before
    assert not any(native in "\n".join(lines) for native in promised)
    assert c1 not in "\n".join(lines)


def test_reverse_orders_boundary_snapshot_before_selected_snapshot(
    tmp_path, monkeypatch, capsys
):
    repo, (_c1, c2, c3), _ = _partial_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _install_no_fetch(monkeypatch)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--boundary",
            "--reverse",
            "--max-count=1",
            "--missing=allow-promisor",
            "HEAD",
        ]
    ) == 0

    assert capsys.readouterr().out.splitlines() == [
        f"-{c2}",
        c3,
        f"{_tree(repo, c2)} ",
        f"{_tree(repo, c3)} ",
    ]


def test_skip_plus_max_count_uses_parent_of_visible_commit_as_boundary(
    tmp_path, monkeypatch, capsys
):
    repo, (c1, c2, c3), _ = _partial_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _install_no_fetch(monkeypatch)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--boundary",
            "--skip=1",
            "--max-count=1",
            "--missing=allow-promisor",
            "HEAD",
        ]
    ) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines == [c2, f"-{c1}", f"{_tree(repo, c2)} ", f"{_tree(repo, c1)} "]
    assert c3 not in "\n".join(lines)


def test_skip_without_truncating_tail_does_not_create_newer_boundary(
    tmp_path, monkeypatch, capsys
):
    repo, (c1, c2, c3), _ = _partial_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _install_no_fetch(monkeypatch)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--boundary",
            "--skip=1",
            "--missing=allow-promisor",
            "HEAD",
        ]
    ) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines == [c2, c1, f"{_tree(repo, c2)} ", f"{_tree(repo, c1)} "]
    assert c3 not in "\n".join(lines)


def test_boundary_count_includes_limit_boundary_snapshot_objects(
    tmp_path, monkeypatch, capsys
):
    repo, (_c1, _c2, _c3), _ = _partial_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _install_no_fetch(monkeypatch)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--boundary",
            "--count",
            "--max-count=1",
            "--missing=allow-promisor",
            "HEAD",
        ]
    ) == 0

    # Selected commit + boundary commit + two distinct local trees. The two
    # promised blobs stay missing and are silently omitted.
    assert capsys.readouterr().out.splitlines() == ["4"]


def test_explicit_exclusion_still_subtracts_boundary_snapshot_closure(
    tmp_path, monkeypatch, capsys
):
    repo, (_c1, c2, c3), _ = _partial_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _install_no_fetch(monkeypatch)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--boundary",
            "--missing=allow-promisor",
            f"{c2}..{c3}",
        ]
    ) == 0

    assert capsys.readouterr().out.splitlines() == [c3, f"-{c2}", f"{_tree(repo, c3)} "]
