from __future__ import annotations

import hashlib

import pytest

from pygit.fetch_importer import PromisorFilteredNativeImporter
from pygit.objects import BlobObject
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


def _commit_data(tree_oid: str, message: str) -> bytes:
    return (
        f"tree {tree_oid}\n"
        "author Test <test@example.com> 1 +0000\n"
        "committer Test <test@example.com> 1 +0000\n"
        f"\n{message}"
    ).encode()


def _partial_diff_repo(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")

    blobs = {
        "left_a": b"left a\n",
        "right_a": b"right a\n",
        "common": b"same b\n",
    }
    blob_oids = {name: _native_oid("blob", data) for name, data in blobs.items()}

    left_tree_data = _tree_data(
        {"a.txt": blob_oids["left_a"], "b.txt": blob_oids["common"]}
    )
    right_tree_data = _tree_data(
        {"a.txt": blob_oids["right_a"], "b.txt": blob_oids["common"]}
    )
    left_tree_oid = _native_oid("tree", left_tree_data)
    right_tree_oid = _native_oid("tree", right_tree_data)
    left_commit_data = _commit_data(left_tree_oid, "left")
    right_commit_data = _commit_data(right_tree_oid, "right")
    left_commit_oid = _native_oid("commit", left_commit_data)
    right_commit_oid = _native_oid("commit", right_commit_data)

    importer = PromisorFilteredNativeImporter(
        repo.store,
        {
            left_tree_oid: NativeObject("tree", left_tree_data, left_tree_oid),
            right_tree_oid: NativeObject("tree", right_tree_data, right_tree_oid),
            left_commit_oid: NativeObject("commit", left_commit_data, left_commit_oid),
            right_commit_oid: NativeObject("commit", right_commit_data, right_commit_oid),
        },
        remote="origin",
        filter_spec="blob:none",
    )
    left_local = importer.import_oid(left_commit_oid)
    right_local = importer.import_oid(right_commit_oid)
    repo.refs.set_branch("left", left_local, message="test: left")
    repo.refs.set_branch("right", right_local, message="test: right")
    repo.refs.set_head_symbolic("left", message="test: left head")

    return repo, blobs, blob_oids, left_local, right_local


def _install_bulk_fetch(monkeypatch, blobs, blob_oids, calls):
    by_oid = {blob_oids[name]: data for name, data in blobs.items()}

    def fake_many(url, oids, *, server_options=()):
        wanted = tuple(oids)
        calls.append((url, wanted, tuple(server_options)))
        return {oid: NativeObject("blob", by_oid[oid], oid) for oid in wanted}

    monkeypatch.setattr("pygit.promisor_materialize._fetch_native_objects", fake_many)
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_object",
        lambda *args, **kwargs: pytest.fail("multiple diff promises must batch"),
    )


def test_commit_to_commit_diff_batches_union_of_snapshot_promises(tmp_path, monkeypatch):
    repo, blobs, blob_oids, _left, _right = _partial_diff_repo(tmp_path)
    repo.config_set("remote", "origin.serverOption", "trace=diff")
    calls = []
    _install_bulk_fetch(monkeypatch, blobs, blob_oids, calls)

    output = repo.diff(from_ref="left", to_ref="right")

    assert "-left a" in output
    assert "+right a" in output
    assert len(calls) == 1
    assert calls[0][0] == "https://example.test/repo.git"
    assert set(calls[0][1]) == set(blob_oids.values())
    assert calls[0][2] == ("trace=diff",)
    state = read_promisor_state(repo.pygit_dir)
    assert set(blob_oids.values()).issubset(state["resolved"])


def test_cached_diff_batches_head_promises_before_tree_flatten(tmp_path, monkeypatch):
    repo, blobs, blob_oids, _left, _right = _partial_diff_repo(tmp_path)

    # Build a fully local SHA-256-native index representing the right snapshot.
    right_a = repo.store.write(BlobObject(blobs["right_a"]))
    common = repo.store.write(BlobObject(blobs["common"]))
    repo.index.entries = {
        "a.txt": repo._index_entry_for_blob("a.txt", right_a, "100644"),
        "b.txt": repo._index_entry_for_blob("b.txt", common, "100644"),
    }
    repo.index.save()

    calls = []
    _install_bulk_fetch(monkeypatch, blobs, blob_oids, calls)

    output = repo.diff(cached=True)

    assert "-left a" in output
    assert "+right a" in output
    assert len(calls) == 1
    assert set(calls[0][1]) == {blob_oids["left_a"], blob_oids["common"]}


def test_worktree_only_diff_does_not_prefetch_unrelated_promises(tmp_path, monkeypatch):
    repo, blobs, _blob_oids, _left, _right = _partial_diff_repo(tmp_path)
    local = repo.store.write(BlobObject(b"index content\n"))
    repo.index.entries = {
        "local.txt": repo._index_entry_for_blob("local.txt", local, "100644")
    }
    repo.index.save()
    (repo.worktree / "local.txt").write_text("working content\n", encoding="utf-8")

    monkeypatch.setattr(
        "pygit.promisor_diff.materialize_promised_objects",
        lambda *args, **kwargs: pytest.fail("worktree-only diff must not prefetch history"),
    )

    output = repo.diff()
    assert "-index content" in output
    assert "+working content" in output


def test_ordinary_commit_diff_stays_network_free(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "ordinary"))
    (repo.worktree / "file.txt").write_text("one\n", encoding="utf-8")
    repo.add(["file.txt"])
    one = repo.commit("one", author_name="Test", author_email="test@example.com")
    (repo.worktree / "file.txt").write_text("two\n", encoding="utf-8")
    repo.add(["file.txt"])
    two = repo.commit("two", author_name="Test", author_email="test@example.com")

    monkeypatch.setattr(
        "pygit.promisor_diff.materialize_promised_objects",
        lambda *args, **kwargs: pytest.fail("ordinary diff must stay network-free"),
    )

    output = repo.diff(from_ref=one, to_ref=two)
    assert "-one" in output
    assert "+two" in output
