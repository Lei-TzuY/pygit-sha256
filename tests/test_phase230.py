from __future__ import annotations

import hashlib

import pytest

from pygit.cat_file import run_batch_commands
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


def _commit_data(tree_oid: str, message: str) -> bytes:
    return (
        f"tree {tree_oid}\n"
        "author Test <test@example.com> 1 +0000\n"
        "committer Test <test@example.com> 1 +0000\n"
        f"\n{message}"
    ).encode()


def _partial_cat_file_repo(tmp_path):
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
    commit_data = _commit_data(tree_oid, "partial")
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
        return NativeObject("blob", by_oid[oid], oid)

    monkeypatch.setattr("pygit.promisor_materialize._fetch_native_objects", fake_many)
    monkeypatch.setattr("pygit.promisor_materialize._fetch_native_object", fake_one)


def test_buffered_batch_command_prefetches_flush_group_once(tmp_path, monkeypatch):
    repo, blobs, blob_oids = _partial_cat_file_repo(tmp_path)
    repo.config_set("remote", "origin.serverOption", "trace=cat-file")
    many_calls = []
    one_calls = []
    _install_fetches(monkeypatch, blobs, blob_oids, many_calls, one_calls)

    chunks = list(
        run_batch_commands(
            repo,
            [
                "info HEAD:a.txt\n",
                "contents HEAD:b.txt\n",
                "flush\n",
            ],
            buffered=True,
        )
    )

    assert len(chunks) == 1
    assert b"alpha" not in chunks[0]
    assert b"bravo\n" in chunks[0]
    assert len(many_calls) == 1
    assert many_calls[0][0] == "https://example.test/repo.git"
    assert set(many_calls[0][1]) == {blob_oids["a"], blob_oids["b"]}
    assert many_calls[0][2] == ("trace=cat-file",)
    assert one_calls == []

    state = read_promisor_state(repo.pygit_dir)
    assert blob_oids["a"] in state["resolved"]
    assert blob_oids["b"] in state["resolved"]
    assert blob_oids["c"] in state["promised"]


def test_buffered_flush_boundaries_do_not_prefetch_future_group(tmp_path, monkeypatch):
    repo, blobs, blob_oids = _partial_cat_file_repo(tmp_path)
    many_calls = []
    one_calls = []
    _install_fetches(monkeypatch, blobs, blob_oids, many_calls, one_calls)

    chunks = list(
        run_batch_commands(
            repo,
            [
                "contents HEAD:a.txt\n",
                "flush\n",
                "contents HEAD:b.txt\n",
                "contents HEAD:c.txt\n",
                "flush\n",
            ],
            buffered=True,
        )
    )

    assert len(chunks) == 2
    assert b"alpha\n" in chunks[0]
    assert b"bravo\n" not in chunks[0]
    assert b"bravo\n" in chunks[1]
    assert b"charlie\n" in chunks[1]
    assert len(one_calls) == 1
    assert one_calls[0][1] == blob_oids["a"]
    assert len(many_calls) == 1
    assert set(many_calls[0][1]) == {blob_oids["b"], blob_oids["c"]}


def test_invalid_blob_peel_does_not_speculatively_fetch(tmp_path, monkeypatch):
    repo, _blobs, blob_oids = _partial_cat_file_repo(tmp_path)

    monkeypatch.setattr(
        "pygit.promisor_cat_file.materialize_promised_objects",
        lambda *args, **kwargs: pytest.fail("invalid tree peel must not prefetch blob"),
    )

    chunks = list(
        run_batch_commands(
            repo,
            ["info HEAD:a.txt^{tree}\n", "flush\n"],
            buffered=True,
        )
    )

    assert chunks == [b"HEAD:a.txt^{tree} missing\n"]
    state = read_promisor_state(repo.pygit_dir)
    assert blob_oids["a"] in state["promised"]


def test_nonbuffered_batch_command_stays_on_historical_path(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "ordinary"))
    (repo.worktree / "file.txt").write_text("local\n", encoding="utf-8")
    repo.add(["file.txt"])
    repo.commit("local", author_name="Test", author_email="test@example.com")

    monkeypatch.setattr(
        "pygit.promisor_cat_file.prefetch_cat_file_promises",
        lambda *args, **kwargs: pytest.fail("nonbuffered cat-file must not use flush batching"),
    )

    chunks = list(
        run_batch_commands(
            repo,
            ["contents HEAD:file.txt\n"],
            buffered=False,
        )
    )
    assert len(chunks) == 1
    assert b"local\n" in chunks[0]


def test_duplicate_expressions_are_deduplicated_before_fetch(tmp_path, monkeypatch):
    repo, blobs, blob_oids = _partial_cat_file_repo(tmp_path)
    many_calls = []
    one_calls = []
    _install_fetches(monkeypatch, blobs, blob_oids, many_calls, one_calls)

    chunks = list(
        run_batch_commands(
            repo,
            [
                "info HEAD:a.txt\n",
                "contents HEAD:a.txt\n",
                "flush\n",
            ],
            buffered=True,
        )
    )

    assert len(chunks) == 1
    assert many_calls == []
    assert len(one_calls) == 1
    assert one_calls[0][1] == blob_oids["a"]
