from __future__ import annotations

import hashlib

import pytest

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


def _commit_data(tree_oid: str, message: str, parents=()) -> bytes:
    lines = [f"tree {tree_oid}\n"]
    lines.extend(f"parent {parent}\n" for parent in parents)
    lines.extend(
        [
            "author Test <test@example.com> 1 +0000\n",
            "committer Test <test@example.com> 1 +0000\n",
            f"\n{message}",
        ]
    )
    return "".join(lines).encode()


def _partial_stash_repo(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")

    # Keep the current checked-out HEAD fully local and clean.  Only the stash
    # graph is foreign/filtered, so Phase228 status demand does not overlap the
    # Phase229 stash demand under test.
    (repo.worktree / "current.txt").write_text("current\n", encoding="utf-8")
    repo.add(["current.txt"])
    current_sha = repo.commit(
        "current",
        author_name="Test",
        author_email="test@example.com",
    )

    blobs = {
        "stash_a": b"stash a\n",
        "index_a": b"index a\n",
        "common": b"common b\n",
    }
    blob_oids = {name: _native_oid("blob", data) for name, data in blobs.items()}

    empty_tree_data = b""
    index_tree_data = _tree_data(
        {"a.txt": blob_oids["index_a"], "b.txt": blob_oids["common"]}
    )
    stash_tree_data = _tree_data(
        {"a.txt": blob_oids["stash_a"], "b.txt": blob_oids["common"]}
    )
    empty_tree_oid = _native_oid("tree", empty_tree_data)
    index_tree_oid = _native_oid("tree", index_tree_data)
    stash_tree_oid = _native_oid("tree", stash_tree_data)

    base_data = _commit_data(empty_tree_oid, "base")
    base_oid = _native_oid("commit", base_data)
    index_data = _commit_data(index_tree_oid, "index on WIP", parents=(base_oid,))
    index_oid = _native_oid("commit", index_data)
    stash_data = _commit_data(
        stash_tree_oid,
        "WIP",
        parents=(base_oid, index_oid),
    )
    stash_oid = _native_oid("commit", stash_data)

    importer = PromisorFilteredNativeImporter(
        repo.store,
        {
            empty_tree_oid: NativeObject("tree", empty_tree_data, empty_tree_oid),
            index_tree_oid: NativeObject("tree", index_tree_data, index_tree_oid),
            stash_tree_oid: NativeObject("tree", stash_tree_data, stash_tree_oid),
            base_oid: NativeObject("commit", base_data, base_oid),
            index_oid: NativeObject("commit", index_data, index_oid),
            stash_oid: NativeObject("commit", stash_data, stash_oid),
        },
        remote="origin",
        filter_spec="blob:none",
    )
    stash_local = importer.import_oid(stash_oid)
    index_local = importer.import_oid(index_oid)
    repo.refs.set_stash(stash_local, message="test: foreign stash")

    return {
        "repo": repo,
        "current": current_sha,
        "stash": stash_local,
        "index": index_local,
        "blobs": blobs,
        "blob_oids": blob_oids,
    }


def _install_bulk_fetch(monkeypatch, fixture, calls):
    by_oid = {
        fixture["blob_oids"][name]: data
        for name, data in fixture["blobs"].items()
    }

    def fake_many(url, oids, *, server_options=()):
        wanted = tuple(oids)
        calls.append((url, wanted, tuple(server_options)))
        return {
            oid: NativeObject("blob", by_oid[oid], oid)
            for oid in wanted
        }

    monkeypatch.setattr("pygit.promisor_materialize._fetch_native_objects", fake_many)
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_object",
        lambda *args, **kwargs: pytest.fail("multiple stash promises must batch"),
    )


def test_stash_pop_batches_only_stash_snapshot_before_restore(tmp_path, monkeypatch):
    fx = _partial_stash_repo(tmp_path)
    repo = fx["repo"]
    repo.config_set("remote", "origin.serverOption", "trace=stash")
    calls = []
    _install_bulk_fetch(monkeypatch, fx, calls)

    result = repo.stash_pop()

    assert result == fx["stash"]
    assert len(calls) == 1
    assert calls[0][0] == "https://example.test/repo.git"
    assert set(calls[0][1]) == {
        fx["blob_oids"]["stash_a"],
        fx["blob_oids"]["common"],
    }
    assert calls[0][2] == ("trace=stash",)
    assert (repo.worktree / "a.txt").read_bytes() == fx["blobs"]["stash_a"]
    assert (repo.worktree / "b.txt").read_bytes() == fx["blobs"]["common"]
    assert not (repo.worktree / "current.txt").exists()
    assert repo.refs.get_stash() is None

    state = read_promisor_state(repo.pygit_dir)
    assert fx["blob_oids"]["index_a"] in state["promised"]


def test_stash_apply_restore_index_batches_union_and_deduplicates(tmp_path, monkeypatch):
    fx = _partial_stash_repo(tmp_path)
    repo = fx["repo"]
    calls = []
    _install_bulk_fetch(monkeypatch, fx, calls)

    result = repo.stash_apply(restore_index=True)

    assert result == fx["stash"]
    assert len(calls) == 1
    assert set(calls[0][1]) == set(fx["blob_oids"].values())
    assert (repo.worktree / "a.txt").read_bytes() == fx["blobs"]["stash_a"]
    assert (repo.worktree / "b.txt").read_bytes() == fx["blobs"]["common"]
    assert repo.refs.get_stash() == fx["stash"]

    state = read_promisor_state(repo.pygit_dir)
    assert set(fx["blob_oids"].values()).issubset(state["resolved"])
    assert repo.index.get("a.txt").sha == state["resolved"][fx["blob_oids"]["index_a"]]
    assert repo.index.get("b.txt").sha == state["resolved"][fx["blob_oids"]["common"]]


def test_stash_prefetch_failure_is_before_worktree_index_or_ref_mutation(tmp_path, monkeypatch):
    fx = _partial_stash_repo(tmp_path)
    repo = fx["repo"]
    before_head = repo.refs.resolve_head()
    before_stash = repo.refs.get_stash()
    before_index = {
        path: (entry.sha, entry.mode)
        for path, entry in repo.index.entries.items()
    }
    before_current = (repo.worktree / "current.txt").read_bytes()

    monkeypatch.setattr(
        "pygit.promisor_stash.prefetch_history_promises",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("promisor offline")),
    )

    with pytest.raises(RuntimeError, match="promisor offline"):
        repo.stash_apply(restore_index=True)

    assert repo.refs.resolve_head() == before_head
    assert repo.refs.get_stash() == before_stash
    assert {
        path: (entry.sha, entry.mode)
        for path, entry in repo.index.entries.items()
    } == before_index
    assert (repo.worktree / "current.txt").read_bytes() == before_current
    assert not (repo.worktree / "a.txt").exists()
    assert not (repo.worktree / "b.txt").exists()


def test_dirty_worktree_rejects_before_stash_prefetch(tmp_path, monkeypatch):
    fx = _partial_stash_repo(tmp_path)
    repo = fx["repo"]
    (repo.worktree / "current.txt").write_text("dirty\n", encoding="utf-8")

    monkeypatch.setattr(
        "pygit.promisor_stash.prefetch_history_promises",
        lambda *args, **kwargs: pytest.fail("dirty stash apply must not prefetch stash"),
    )

    with pytest.raises(RuntimeError, match="local changes"):
        repo.stash_apply()


def test_invalid_stash_index_rejects_before_prefetch(tmp_path, monkeypatch):
    fx = _partial_stash_repo(tmp_path)
    repo = fx["repo"]
    monkeypatch.setattr(
        "pygit.promisor_stash.prefetch_history_promises",
        lambda *args, **kwargs: pytest.fail("invalid stash index must not prefetch"),
    )

    with pytest.raises(RuntimeError, match="No stash entry found at index 5"):
        repo.stash_apply(index=5)


def test_ordinary_local_stash_stays_out_of_promisor_layer(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "ordinary"))
    (repo.worktree / "file.txt").write_text("base\n", encoding="utf-8")
    repo.add(["file.txt"])
    repo.commit("base", author_name="Test", author_email="test@example.com")
    (repo.worktree / "file.txt").write_text("stashed\n", encoding="utf-8")
    repo.stash_push(author_name="Test", author_email="test@example.com")

    monkeypatch.setattr(
        "pygit.promisor_stash.prefetch_history_promises",
        lambda *args, **kwargs: pytest.fail("ordinary stash must stay network-free"),
    )

    repo.stash_apply()
    assert (repo.worktree / "file.txt").read_text(encoding="utf-8") == "stashed\n"
