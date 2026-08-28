from __future__ import annotations

import hashlib

import pytest

from pygit import promisor_worktree
from pygit.fetch_importer import PromisorFilteredNativeImporter
from pygit.promisor import read_promisor_state
from pygit.remote import NativeObject
from pygit.repo import Repository


def _native_oid(type_name: str, data: bytes) -> str:
    return hashlib.sha1(f"{type_name} {len(data)}\0".encode() + data).hexdigest()


def _native_commit(
    tree_oid: str,
    *,
    parent_oid: str | None = None,
    message: str = "commit",
) -> tuple[str, NativeObject]:
    parent = f"parent {parent_oid}\n" if parent_oid else ""
    data = (
        f"tree {tree_oid}\n"
        f"{parent}"
        "author Test <test@example.com> 1 +0000\n"
        "committer Test <test@example.com> 1 +0000\n"
        f"\n{message}"
    ).encode()
    oid = _native_oid("commit", data)
    return oid, NativeObject("commit", data, oid)


def _promisor_fast_forward_repo(tmp_path, files: dict[str, bytes]):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")

    empty_tree_data = b""
    empty_tree_oid = _native_oid("tree", empty_tree_data)
    root_oid, root_obj = _native_commit(empty_tree_oid, message="root")

    blob_oids = {name: _native_oid("blob", data) for name, data in files.items()}
    child_tree_data = b"".join(
        b"100644 " + name.encode() + b"\x00" + bytes.fromhex(blob_oids[name])
        for name in sorted(files)
    )
    child_tree_oid = _native_oid("tree", child_tree_data)
    child_oid, child_obj = _native_commit(
        child_tree_oid,
        parent_oid=root_oid,
        message="child",
    )

    objects = {
        empty_tree_oid: NativeObject("tree", empty_tree_data, empty_tree_oid),
        root_oid: root_obj,
        child_tree_oid: NativeObject("tree", child_tree_data, child_tree_oid),
        child_oid: child_obj,
    }
    importer = PromisorFilteredNativeImporter(
        repo.store,
        objects,
        remote="origin",
        filter_spec="blob:none",
    )
    root_local = importer.import_oid(root_oid)
    child_local = importer.import_oid(child_oid)

    repo.refs.set_branch("main", root_local, message="test: root")
    repo.refs.set_branch("topic", child_local, message="test: child")
    repo.refs.set_head_symbolic("main", message="test: main")
    return repo, root_local, child_local, blob_oids


def test_fast_forward_merge_batches_promised_target_blobs(tmp_path, monkeypatch):
    files = {"a.txt": b"alpha\n", "b.txt": b"beta\n"}
    repo, _root, child, blob_oids = _promisor_fast_forward_repo(tmp_path, files)
    calls = []

    by_oid = {blob_oids[name]: data for name, data in files.items()}

    def fake_many(url, oids, *, server_options=()):
        wanted = tuple(oids)
        calls.append((url, wanted, tuple(server_options)))
        return {oid: NativeObject("blob", by_oid[oid], oid) for oid in wanted}

    monkeypatch.setattr("pygit.promisor_materialize._fetch_native_objects", fake_many)
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_object",
        lambda *args, **kwargs: pytest.fail("multi-blob fast-forward must batch"),
    )

    result = repo.merge("topic")

    assert result["status"] == "fast-forward"
    assert result["sha"] == child
    assert repo.refs.resolve_head() == child
    assert (repo.worktree / "a.txt").read_bytes() == files["a.txt"]
    assert (repo.worktree / "b.txt").read_bytes() == files["b.txt"]
    assert len(calls) == 1
    assert calls[0][0] == "https://example.test/repo.git"
    assert set(calls[0][1]) == set(blob_oids.values())
    state = read_promisor_state(repo.pygit_dir)
    assert state["promised"] == {}
    assert set(blob_oids.values()) <= set(state["resolved"])


def test_full_replacement_one_blob_preserves_single_fetch_seam(tmp_path, monkeypatch):
    files = {"one.txt": b"one\n"}
    repo, _root, child, blob_oids = _promisor_fast_forward_repo(tmp_path, files)
    native_oid = blob_oids["one.txt"]
    calls = []

    def fake_one(url, oid, *, server_options=()):
        calls.append((url, oid, tuple(server_options)))
        return {oid: NativeObject("blob", files["one.txt"], oid)}

    monkeypatch.setattr("pygit.promisor_materialize._fetch_native_object", fake_one)
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_objects",
        lambda *args, **kwargs: pytest.fail("single replacement promise must keep Phase213 seam"),
    )

    repo._replace_worktree_from_commit(child)

    assert calls == [("https://example.test/repo.git", native_oid, ())]
    assert (repo.worktree / "one.txt").read_bytes() == files["one.txt"]


def test_materialization_failure_precedes_fast_forward_mutation(tmp_path, monkeypatch):
    repo, root, _child, blob_oids = _promisor_fast_forward_repo(
        tmp_path,
        {"blocked.txt": b"blocked\n"},
    )

    def fail(*args, **kwargs):
        raise RuntimeError("promisor unavailable")

    monkeypatch.setattr(promisor_worktree, "materialize_promised_objects", fail)

    with pytest.raises(RuntimeError, match="promisor unavailable"):
        repo.merge("topic")

    assert repo.refs.resolve_head() == root
    assert repo.refs.get_branch("main") == root
    assert not (repo.worktree / "blocked.txt").exists()
    assert list(repo.index.paths()) == []
    state = read_promisor_state(repo.pygit_dir)
    assert next(iter(blob_oids.values())) in state["promised"]


def test_unrelated_promises_do_not_fetch_for_empty_target_snapshot(tmp_path, monkeypatch):
    repo, root, _child, blob_oids = _promisor_fast_forward_repo(
        tmp_path,
        {"later.txt": b"later\n"},
    )
    monkeypatch.setattr(
        promisor_worktree,
        "materialize_promised_objects",
        lambda *args, **kwargs: pytest.fail("empty target snapshot needs no promised blobs"),
    )

    repo._replace_worktree_from_commit(root)

    assert list(repo.index.paths()) == []
    assert not (repo.worktree / "later.txt").exists()
    state = read_promisor_state(repo.pygit_dir)
    assert next(iter(blob_oids.values())) in state["promised"]


def test_ordinary_full_replacement_stays_network_free(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "ordinary"))
    (repo.worktree / "local.txt").write_text("local\n", encoding="utf-8")
    repo.add(["local.txt"])
    sha = repo.commit("local", author_name="Test", author_email="test@example.com")
    (repo.worktree / "local.txt").write_text("dirty\n", encoding="utf-8")

    monkeypatch.setattr(
        promisor_worktree,
        "materialize_promised_objects",
        lambda *args, **kwargs: pytest.fail("ordinary replacement must not materialize"),
    )

    repo._replace_worktree_from_commit(sha)
    assert (repo.worktree / "local.txt").read_text(encoding="utf-8") == "local\n"
