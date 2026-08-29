from __future__ import annotations

import hashlib

import pytest

from pygit.fetch_importer import PromisorFilteredNativeImporter
from pygit.ls_tree import ls_tree
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


def _commit_data(tree_oid: str) -> bytes:
    return (
        f"tree {tree_oid}\n"
        "author Test <test@example.com> 1 +0000\n"
        "committer Test <test@example.com> 1 +0000\n"
        "\npartial ls-tree"
    ).encode()


def _partial_repo(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")

    blobs = {
        "a": b"alpha\n",
        "b": b"bravo\n",
        "c": b"charlie\n",
    }
    blob_oids = {name: _native_oid("blob", data) for name, data in blobs.items()}
    tree_data = _tree_data(
        {
            "a.txt": blob_oids["a"],
            "b.txt": blob_oids["b"],
            "c.txt": blob_oids["c"],
        }
    )
    tree_oid = _native_oid("tree", tree_data)
    commit_data = _commit_data(tree_oid)
    commit_oid = _native_oid("commit", commit_data)

    importer = PromisorFilteredNativeImporter(
        repo.store,
        {
            tree_oid: NativeObject("tree", tree_data, tree_oid),
            commit_oid: NativeObject("commit", commit_data, commit_oid),
        },
        remote="origin",
        filter_spec="blob:none",
    )
    local_commit = importer.import_oid(commit_oid)
    repo.refs.set_branch("main", local_commit, message="test: partial head")
    repo.refs.set_head_symbolic("main", message="test: partial head")
    return repo, blobs, blob_oids


def _install_fetches(monkeypatch, blobs, blob_oids, many_calls, one_calls):
    by_oid = {blob_oids[name]: data for name, data in blobs.items()}

    def fake_many(url, oids, *, server_options=()):
        wanted = tuple(oids)
        many_calls.append((url, wanted, tuple(server_options)))
        return {oid: NativeObject("blob", by_oid[oid], oid) for oid in wanted}

    def fake_one(url, oid, *, server_options=()):
        one_calls.append((url, oid, tuple(server_options)))
        return {oid: NativeObject("blob", by_oid[oid], oid)}

    monkeypatch.setattr("pygit.promisor_materialize._fetch_native_objects", fake_many)
    monkeypatch.setattr("pygit.promisor_materialize._fetch_native_object", fake_one)


def test_ls_tree_batches_selected_promises_once(tmp_path, monkeypatch):
    repo, blobs, blob_oids = _partial_repo(tmp_path)
    repo.config_set("remote", "origin.serverOption", "trace=ls-tree")
    many_calls = []
    one_calls = []
    _install_fetches(monkeypatch, blobs, blob_oids, many_calls, one_calls)

    entries = ls_tree(repo, "HEAD", patterns=("a*", "b*"))

    assert [entry.path for entry in entries] == ["a.txt", "b.txt"]
    assert all(len(entry.oid) == 64 for entry in entries)
    assert len(many_calls) == 1
    assert many_calls[0][0] == "https://example.test/repo.git"
    assert set(many_calls[0][1]) == {blob_oids["a"], blob_oids["b"]}
    assert many_calls[0][2] == ("trace=ls-tree",)
    assert one_calls == []

    state = read_promisor_state(repo.pygit_dir)
    assert blob_oids["a"] in state["resolved"]
    assert blob_oids["b"] in state["resolved"]
    assert blob_oids["c"] in state["promised"]


def test_ls_tree_single_selected_promise_preserves_single_fetch_seam(tmp_path, monkeypatch):
    repo, blobs, blob_oids = _partial_repo(tmp_path)
    many_calls = []
    one_calls = []
    _install_fetches(monkeypatch, blobs, blob_oids, many_calls, one_calls)

    entries = ls_tree(repo, "HEAD", patterns=("c.txt",))

    assert [entry.path for entry in entries] == ["c.txt"]
    assert many_calls == []
    assert len(one_calls) == 1
    assert one_calls[0][1] == blob_oids["c"]
    state = read_promisor_state(repo.pygit_dir)
    assert blob_oids["a"] in state["promised"]
    assert blob_oids["b"] in state["promised"]


def test_ls_tree_ordinary_repository_never_prefetches(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "ordinary"))
    (repo.worktree / "local.txt").write_text("local\n", encoding="utf-8")
    repo.add(["local.txt"])
    repo.commit("local", author_name="Test", author_email="test@example.com")

    monkeypatch.setattr(
        "pygit.ls_tree.materialize_promised_objects",
        lambda *args, **kwargs: pytest.fail("ordinary ls-tree must not prefetch"),
    )

    entries = ls_tree(repo, "HEAD")
    assert [entry.path for entry in entries] == ["local.txt"]
