from __future__ import annotations

import hashlib

import pytest

from pygit import promisor_reset
from pygit.fetch_importer import PromisorFilteredNativeImporter
from pygit.promisor import read_promisor_state
from pygit.remote import NativeObject
from pygit.repo import Repository


def _native_oid(type_name: str, data: bytes) -> str:
    return hashlib.sha1(f"{type_name} {len(data)}\0".encode() + data).hexdigest()


def _native_commit(tree_oid: str) -> tuple[str, NativeObject]:
    data = (
        f"tree {tree_oid}\n"
        "author Test <test@example.com> 1 +0000\n"
        "committer Test <test@example.com> 1 +0000\n"
        "\ntip"
    ).encode()
    oid = _native_oid("commit", data)
    return oid, NativeObject("commit", data, oid)


def _promisor_repo(tmp_path, files: dict[str, bytes]):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")

    blob_oids = {name: _native_oid("blob", data) for name, data in files.items()}
    tree_data = b"".join(
        b"100644 " + name.encode() + b"\x00" + bytes.fromhex(blob_oids[name])
        for name in sorted(files)
    )
    tree_oid = _native_oid("tree", tree_data)
    commit_oid, commit_obj = _native_commit(tree_oid)
    tree_obj = NativeObject("tree", tree_data, tree_oid)

    importer = PromisorFilteredNativeImporter(
        repo.store,
        {commit_oid: commit_obj, tree_oid: tree_obj},
        remote="origin",
        filter_spec="blob:none",
    )
    local_commit = importer.import_oid(commit_oid)
    repo.refs.set_branch("main", local_commit, message="test: promisor main")
    repo.refs.set_head_symbolic("main", message="test: promisor main")
    return repo, blob_oids


def test_hard_reset_batches_multiple_promised_blobs(tmp_path, monkeypatch):
    files = {"a.txt": b"alpha\n", "b.txt": b"beta\n"}
    repo, blob_oids = _promisor_repo(tmp_path, files)
    calls = []

    def fake_many(url, oids, *, server_options=()):
        wanted = tuple(oids)
        calls.append((url, wanted, tuple(server_options)))
        by_oid = {blob_oids[name]: data for name, data in files.items()}
        return {oid: NativeObject("blob", by_oid[oid], oid) for oid in wanted}

    def fail_single(*args, **kwargs):
        raise AssertionError("multi-blob hard reset must use one batch fetch")

    monkeypatch.setattr("pygit.promisor_materialize._fetch_native_objects", fake_many)
    monkeypatch.setattr("pygit.promisor_materialize._fetch_native_object", fail_single)

    repo.reset("HEAD", mode="hard")

    assert (repo.worktree / "a.txt").read_bytes() == files["a.txt"]
    assert (repo.worktree / "b.txt").read_bytes() == files["b.txt"]
    assert len(calls) == 1
    assert set(calls[0][1]) == set(blob_oids.values())
    state = read_promisor_state(repo.pygit_dir)
    assert state["promised"] == {}
    assert set(blob_oids.values()) <= set(state["resolved"])


def test_hard_reset_one_promise_preserves_single_fetch_seam(tmp_path, monkeypatch):
    files = {"one.txt": b"one\n"}
    repo, blob_oids = _promisor_repo(tmp_path, files)
    native_oid = blob_oids["one.txt"]
    calls = []

    def fake_one(url, oid, *, server_options=()):
        calls.append((url, oid, tuple(server_options)))
        return {oid: NativeObject("blob", files["one.txt"], oid)}

    monkeypatch.setattr("pygit.promisor_materialize._fetch_native_object", fake_one)
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_objects",
        lambda *args, **kwargs: pytest.fail("single promise must keep Phase213 seam"),
    )

    repo.reset(mode="hard")
    assert calls == [("https://example.test/repo.git", native_oid, ())]
    assert (repo.worktree / "one.txt").read_bytes() == files["one.txt"]


def test_mixed_reset_batches_promises_for_sha256_index(tmp_path, monkeypatch):
    files = {"a.txt": b"alpha\n", "b.txt": b"beta\n"}
    repo, blob_oids = _promisor_repo(tmp_path, files)
    calls = []

    def fake_many(url, oids, *, server_options=()):
        wanted = tuple(oids)
        calls.append((url, wanted, tuple(server_options)))
        by_oid = {blob_oids[name]: data for name, data in files.items()}
        return {oid: NativeObject("blob", by_oid[oid], oid) for oid in wanted}

    monkeypatch.setattr("pygit.promisor_materialize._fetch_native_objects", fake_many)
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_object",
        lambda *args, **kwargs: pytest.fail("multi-blob mixed reset must batch"),
    )

    repo.reset("HEAD", mode="mixed")

    assert len(calls) == 1
    assert set(calls[0][1]) == set(blob_oids.values())
    assert set(repo.index.paths()) == set(files)
    assert not (repo.worktree / "a.txt").exists()
    assert not (repo.worktree / "b.txt").exists()
    state = read_promisor_state(repo.pygit_dir)
    assert state["promised"] == {}
    assert set(blob_oids.values()) <= set(state["resolved"])


def test_soft_reset_does_not_materialize_promises(tmp_path, monkeypatch):
    repo, blob_oids = _promisor_repo(tmp_path, {"later.txt": b"later\n"})
    monkeypatch.setattr(
        promisor_reset,
        "materialize_promised_objects",
        lambda *args, **kwargs: pytest.fail("soft reset must not fetch blobs"),
    )

    repo.reset("HEAD", mode="soft")
    state = read_promisor_state(repo.pygit_dir)
    assert next(iter(blob_oids.values())) in state["promised"]


@pytest.mark.parametrize("mode", ["mixed", "hard"])
def test_materialization_failure_precedes_reset_mutation(tmp_path, monkeypatch, mode):
    repo, _ = _promisor_repo(tmp_path, {"blocked.txt": b"blocked\n"})
    before = repo.refs.resolve_head()
    assert list(repo.index.paths()) == []

    def fail(*args, **kwargs):
        raise RuntimeError("promisor unavailable")

    monkeypatch.setattr(promisor_reset, "materialize_promised_objects", fail)

    with pytest.raises(RuntimeError, match="promisor unavailable"):
        repo.reset("HEAD", mode=mode)

    assert repo.refs.resolve_head() == before
    assert list(repo.index.paths()) == []
    assert not (repo.worktree / "blocked.txt").exists()


def test_ordinary_hard_reset_stays_network_free(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "ordinary"))
    (repo.worktree / "local.txt").write_text("local\n", encoding="utf-8")
    repo.add(["local.txt"])
    repo.commit("local", author_name="Test", author_email="test@example.com")
    (repo.worktree / "local.txt").write_text("dirty\n", encoding="utf-8")

    monkeypatch.setattr(
        promisor_reset,
        "materialize_promised_objects",
        lambda *args, **kwargs: pytest.fail("ordinary reset must not materialize"),
    )

    repo.reset(mode="hard")
    assert (repo.worktree / "local.txt").read_text(encoding="utf-8") == "local\n"
