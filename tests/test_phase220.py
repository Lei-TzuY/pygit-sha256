from __future__ import annotations

import hashlib

import pytest

from pygit import promisor_reset_paths
from pygit.fetch_importer import PromisorFilteredNativeImporter
from pygit.objects import BlobObject
from pygit.promisor import read_promisor_state
from pygit.remote import NativeObject
from pygit.repo import Repository


def _native_oid(type_name: str, data: bytes) -> str:
    return hashlib.sha1(f"{type_name} {len(data)}\0".encode() + data).hexdigest()


def _partial_path_repo(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")

    files = {
        "dir/a.txt": b"alpha\n",
        "dir/b.txt": b"beta\n",
        "outside.txt": b"outside\n",
    }
    blob_oids = {path: _native_oid("blob", data) for path, data in files.items()}

    dir_tree_data = b"".join(
        b"100644 " + name.encode() + b"\x00" + bytes.fromhex(blob_oids[f"dir/{name}"])
        for name in ("a.txt", "b.txt")
    )
    dir_tree_oid = _native_oid("tree", dir_tree_data)
    root_tree_data = (
        b"40000 dir\x00"
        + bytes.fromhex(dir_tree_oid)
        + b"100644 outside.txt\x00"
        + bytes.fromhex(blob_oids["outside.txt"])
    )
    root_tree_oid = _native_oid("tree", root_tree_data)
    commit_data = (
        f"tree {root_tree_oid}\n"
        "author Test <test@example.com> 1 +0000\n"
        "committer Test <test@example.com> 1 +0000\n"
        "\npartial"
    ).encode()
    commit_oid = _native_oid("commit", commit_data)

    importer = PromisorFilteredNativeImporter(
        repo.store,
        {
            dir_tree_oid: NativeObject("tree", dir_tree_data, dir_tree_oid),
            root_tree_oid: NativeObject("tree", root_tree_data, root_tree_oid),
            commit_oid: NativeObject("commit", commit_data, commit_oid),
        },
        remote="origin",
        filter_spec="blob:none",
    )
    commit_local = importer.import_oid(commit_oid)
    repo.refs.set_branch("main", commit_local, message="test: partial")
    repo.refs.set_head_symbolic("main", message="test: main")
    return repo, files, blob_oids


def test_reset_paths_batches_only_selected_subtree_promises(tmp_path, monkeypatch):
    repo, files, blob_oids = _partial_path_repo(tmp_path)
    calls = []
    by_oid = {blob_oids[path]: data for path, data in files.items()}

    def fake_many(url, oids, *, server_options=()):
        wanted = tuple(oids)
        calls.append((url, wanted, tuple(server_options)))
        return {oid: NativeObject("blob", by_oid[oid], oid) for oid in wanted}

    monkeypatch.setattr("pygit.promisor_materialize._fetch_native_objects", fake_many)
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_object",
        lambda *args, **kwargs: pytest.fail("two selected blobs must batch"),
    )

    result = repo.reset_paths(["dir"], target="main")

    assert result["paths"] == ["dir/a.txt", "dir/b.txt"]
    assert set(repo.index.paths()) == {"dir/a.txt", "dir/b.txt"}
    assert len(calls) == 1
    assert set(calls[0][1]) == {blob_oids["dir/a.txt"], blob_oids["dir/b.txt"]}

    state = read_promisor_state(repo.pygit_dir)
    assert blob_oids["outside.txt"] in state["promised"]
    assert blob_oids["dir/a.txt"] in state["resolved"]
    assert blob_oids["dir/b.txt"] in state["resolved"]


def test_reset_paths_single_file_keeps_single_fetch_seam(tmp_path, monkeypatch):
    repo, files, blob_oids = _partial_path_repo(tmp_path)
    wanted_oid = blob_oids["dir/a.txt"]
    calls = []

    def fake_one(url, oid, *, server_options=()):
        calls.append((url, oid, tuple(server_options)))
        return {oid: NativeObject("blob", files["dir/a.txt"], oid)}

    monkeypatch.setattr("pygit.promisor_materialize._fetch_native_object", fake_one)
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_objects",
        lambda *args, **kwargs: pytest.fail("one selected blob must keep single-object seam"),
    )

    result = repo.reset_paths(["dir/a.txt"], target="main")

    assert result["paths"] == ["dir/a.txt"]
    assert calls == [("https://example.test/repo.git", wanted_oid, ())]
    state = read_promisor_state(repo.pygit_dir)
    assert blob_oids["dir/b.txt"] in state["promised"]
    assert blob_oids["outside.txt"] in state["promised"]


def test_reset_paths_validates_all_specs_before_materialization_or_index_change(tmp_path, monkeypatch):
    repo, _files, blob_oids = _partial_path_repo(tmp_path)
    existing_sha = repo.store.write(BlobObject(b"staged\n"))
    repo.index.entries["keep.txt"] = repo._index_entry_for_blob("keep.txt", existing_sha, "100644")
    repo.index.save()
    before = dict(repo.index.entries)

    monkeypatch.setattr(
        promisor_reset_paths,
        "materialize_promised_objects",
        lambda *args, **kwargs: pytest.fail("invalid pathspec must fail before fetch"),
    )

    with pytest.raises(KeyError, match="missing"):
        repo.reset_paths(["dir/a.txt", "missing"], target="main")

    assert repo.index.entries == before
    state = read_promisor_state(repo.pygit_dir)
    assert set(blob_oids.values()) <= set(state["promised"])


def test_reset_paths_index_only_deletion_does_not_fetch_unrelated_promises(tmp_path, monkeypatch):
    repo, _files, blob_oids = _partial_path_repo(tmp_path)
    gone_sha = repo.store.write(BlobObject(b"gone\n"))
    repo.index.entries["gone.txt"] = repo._index_entry_for_blob("gone.txt", gone_sha, "100644")
    repo.index.save()

    monkeypatch.setattr(
        promisor_reset_paths,
        "materialize_promised_objects",
        lambda *args, **kwargs: pytest.fail("index-only deletion must not fetch target blobs"),
    )

    result = repo.reset_paths(["gone.txt"], target="main")

    assert result["paths"] == ["gone.txt"]
    assert "gone.txt" not in repo.index
    state = read_promisor_state(repo.pygit_dir)
    assert set(blob_oids.values()) <= set(state["promised"])


def test_reset_paths_overlapping_specs_deduplicate_promises(tmp_path, monkeypatch):
    repo, files, blob_oids = _partial_path_repo(tmp_path)
    calls = []
    by_oid = {blob_oids[path]: data for path, data in files.items()}

    def fake_many(url, oids, *, server_options=()):
        wanted = tuple(oids)
        calls.append(wanted)
        return {oid: NativeObject("blob", by_oid[oid], oid) for oid in wanted}

    monkeypatch.setattr("pygit.promisor_materialize._fetch_native_objects", fake_many)
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_object",
        lambda *args, **kwargs: pytest.fail("two unique promises should batch"),
    )

    result = repo.reset_paths(["dir", "dir/a.txt"], target="main")

    assert result["paths"] == ["dir/a.txt", "dir/b.txt"]
    assert len(calls) == 1
    assert set(calls[0]) == {blob_oids["dir/a.txt"], blob_oids["dir/b.txt"]}
    assert blob_oids["outside.txt"] in read_promisor_state(repo.pygit_dir)["promised"]


def test_ordinary_reset_paths_keeps_historical_implementation(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "ordinary"))
    (repo.worktree / "a.txt").write_text("base\n", encoding="utf-8")
    repo.add(["a.txt"])
    head = repo.commit("initial", author_name="Test", author_email="test@example.com")
    (repo.worktree / "a.txt").write_text("staged\n", encoding="utf-8")
    repo.add(["a.txt"])

    monkeypatch.setattr(
        promisor_reset_paths,
        "materialize_promised_objects",
        lambda *args, **kwargs: pytest.fail("ordinary reset_paths must not materialize"),
    )

    result = repo.reset_paths(["a.txt"], target=head)
    assert result["paths"] == ["a.txt"]
    assert repo.index.entries["a.txt"].sha == repo._commit_tree_entries(head)["a.txt"][0]
