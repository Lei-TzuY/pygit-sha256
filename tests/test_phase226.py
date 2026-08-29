from __future__ import annotations

import hashlib
from typing import Optional

import pytest

from pygit import promisor_history
from pygit.fetch_importer import PromisorFilteredNativeImporter
from pygit.promisor import read_promisor_state
from pygit.remote import NativeObject
from pygit.repo import Repository


def _native_oid(type_name: str, data: bytes) -> str:
    return hashlib.sha1(f"{type_name} {len(data)}\0".encode() + data).hexdigest()


def _tree_data(entries):
    return b"".join(
        b"100644 " + name.encode() + b"\x00" + bytes.fromhex(oid)
        for name, oid in sorted(entries.items())
    )


def _commit_data(tree_oid: str, message: str, parent: Optional[str] = None) -> bytes:
    lines = [f"tree {tree_oid}\n"]
    if parent:
        lines.append(f"parent {parent}\n")
    lines.extend(
        [
            "author Test <test@example.com> 1 +0000\n",
            "committer Test <test@example.com> 1 +0000\n",
            f"\n{message}",
        ]
    )
    return "".join(lines).encode()


def _partial_history_repo(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")

    blobs = {
        "root_a": b"root a\n",
        "child_a": b"child a\n",
        "common": b"same b\n",
    }
    blob_oids = {name: _native_oid("blob", data) for name, data in blobs.items()}

    root_tree_data = _tree_data(
        {"a.txt": blob_oids["root_a"], "b.txt": blob_oids["common"]}
    )
    child_tree_data = _tree_data(
        {"a.txt": blob_oids["child_a"], "b.txt": blob_oids["common"]}
    )
    root_tree_oid = _native_oid("tree", root_tree_data)
    child_tree_oid = _native_oid("tree", child_tree_data)

    root_commit_data = _commit_data(root_tree_oid, "root")
    root_commit_oid = _native_oid("commit", root_commit_data)
    child_commit_data = _commit_data(child_tree_oid, "child", parent=root_commit_oid)
    child_commit_oid = _native_oid("commit", child_commit_data)

    importer = PromisorFilteredNativeImporter(
        repo.store,
        {
            root_tree_oid: NativeObject("tree", root_tree_data, root_tree_oid),
            child_tree_oid: NativeObject("tree", child_tree_data, child_tree_oid),
            root_commit_oid: NativeObject("commit", root_commit_data, root_commit_oid),
            child_commit_oid: NativeObject("commit", child_commit_data, child_commit_oid),
        },
        remote="origin",
        filter_spec="blob:none",
    )
    root_local = importer.import_oid(root_commit_oid)
    child_local = importer.import_oid(child_commit_oid)
    repo.refs.set_branch("main", child_local, message="test: partial history")
    repo.refs.set_head_symbolic("main", message="test: main")
    return repo, blobs, blob_oids, root_local, child_local


def _install_bulk_fetch(monkeypatch, blobs, blob_oids, calls):
    by_oid = {blob_oids[name]: data for name, data in blobs.items()}

    def fake_many(url, oids, *, server_options=()):
        wanted = tuple(oids)
        calls.append((url, wanted, tuple(server_options)))
        return {oid: NativeObject("blob", by_oid[oid], oid) for oid in wanted}

    monkeypatch.setattr("pygit.promisor_materialize._fetch_native_objects", fake_many)
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_object",
        lambda *args, **kwargs: pytest.fail("multiple history promises must batch"),
    )


def test_show_batches_commit_and_parent_snapshots(tmp_path, monkeypatch):
    repo, blobs, blob_oids, _root, _child = _partial_history_repo(tmp_path)
    repo.config_set("remote", "origin.serverOption", "trace=history")
    calls = []
    _install_bulk_fetch(monkeypatch, blobs, blob_oids, calls)

    output = repo.show("main")

    assert "-root a" in output
    assert "+child a" in output
    assert len(calls) == 1
    assert calls[0][0] == "https://example.test/repo.git"
    assert set(calls[0][1]) == set(blob_oids.values())
    assert calls[0][2] == ("trace=history",)
    assert set(blob_oids.values()).issubset(read_promisor_state(repo.pygit_dir)["resolved"])


def test_log_line_range_batches_reachable_history_snapshots(tmp_path, monkeypatch):
    repo, blobs, blob_oids, root, child = _partial_history_repo(tmp_path)
    calls = []
    _install_bulk_fetch(monkeypatch, blobs, blob_oids, calls)

    entries = repo.log(line_range=(1, 1, "a.txt"))

    assert [sha for sha, _obj in entries] == [child, root]
    assert len(calls) == 1
    assert set(calls[0][1]) == set(blob_oids.values())


def test_log_follow_uses_same_history_batch_planner(tmp_path, monkeypatch):
    repo, blobs, blob_oids, root, child = _partial_history_repo(tmp_path)
    calls = []
    _install_bulk_fetch(monkeypatch, blobs, blob_oids, calls)

    entries = repo.log(follow="a.txt")

    assert [sha for sha, _obj in entries] == [child, root]
    assert len(calls) == 1
    assert set(calls[0][1]) == set(blob_oids.values())


def test_log_metadata_filter_can_avoid_history_prefetch(tmp_path, monkeypatch):
    repo, _blobs, blob_oids, _root, _child = _partial_history_repo(tmp_path)
    monkeypatch.setattr(
        promisor_history,
        "materialize_promised_objects",
        lambda *args, **kwargs: pytest.fail("filtered-out history must not prefetch blobs"),
    )

    entries = repo.log(grep="never-matches", line_range=(1, 1, "a.txt"))

    assert entries == []
    assert set(blob_oids.values()).issubset(read_promisor_state(repo.pygit_dir)["promised"])


def test_plain_log_does_not_prefetch_promised_history(tmp_path, monkeypatch):
    repo, _blobs, blob_oids, root, child = _partial_history_repo(tmp_path)
    monkeypatch.setattr(
        promisor_history,
        "materialize_promised_objects",
        lambda *args, **kwargs: pytest.fail("metadata-only log must remain blob-free"),
    )

    entries = repo.log()

    assert [sha for sha, _obj in entries] == [child, root]
    assert set(blob_oids.values()).issubset(read_promisor_state(repo.pygit_dir)["promised"])


def test_ordinary_show_and_line_log_stay_network_free(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "ordinary"))
    (repo.worktree / "a.txt").write_text("one\n", encoding="utf-8")
    repo.add(["a.txt"])
    root = repo.commit("root", author_name="Test", author_email="test@example.com")
    (repo.worktree / "a.txt").write_text("two\n", encoding="utf-8")
    repo.add(["a.txt"])
    child = repo.commit("child", author_name="Test", author_email="test@example.com")

    monkeypatch.setattr(
        promisor_history,
        "materialize_promised_objects",
        lambda *args, **kwargs: pytest.fail("ordinary history readers must stay network-free"),
    )

    output = repo.show(child)
    entries = repo.log(line_range=(1, 1, "a.txt"))

    assert "-one" in output
    assert "+two" in output
    assert [sha for sha, _obj in entries] == [child, root]
