from __future__ import annotations

import hashlib

import pytest

from pygit import promisor_commit
from pygit.fetch_importer import PromisorFilteredNativeImporter
from pygit.objects import BlobObject
from pygit.promisor import read_promisor_state
from pygit.remote import NativeObject
from pygit.repo import Repository


def _native_oid(type_name: str, data: bytes) -> str:
    return hashlib.sha1(f"{type_name} {len(data)}\0".encode() + data).hexdigest()


def _partial_commit_repo(tmp_path):
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
    return repo, files, blob_oids, commit_local


def test_commit_only_paths_batches_complete_head_promises(tmp_path, monkeypatch):
    repo, files, blob_oids, old_head = _partial_commit_repo(tmp_path)
    calls = []
    by_oid = {blob_oids[path]: data for path, data in files.items()}

    def fake_many(url, oids, *, server_options=()):
        wanted = tuple(oids)
        calls.append((url, wanted, tuple(server_options)))
        return {oid: NativeObject("blob", by_oid[oid], oid) for oid in wanted}

    monkeypatch.setattr("pygit.promisor_materialize._fetch_native_objects", fake_many)
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_object",
        lambda *args, **kwargs: pytest.fail("three HEAD promises must batch"),
    )

    (repo.worktree / "dir").mkdir(parents=True, exist_ok=True)
    (repo.worktree / "dir" / "a.txt").write_text("changed\n", encoding="utf-8")

    new_head = repo.commit(
        "change a only",
        author_name="Test",
        author_email="test@example.com",
        only_paths=["dir/a.txt"],
    )

    assert new_head != old_head
    assert len(calls) == 1
    assert calls[0][0] == "https://example.test/repo.git"
    assert set(calls[0][1]) == set(blob_oids.values())

    state = read_promisor_state(repo.pygit_dir)
    assert set(blob_oids.values()) <= set(state["resolved"])
    assert not (set(blob_oids.values()) & set(state["promised"]))

    old_tree = repo._commit_tree_entries(old_head)
    new_tree = repo._commit_tree_entries(new_head)
    changed_sha = repo.store.write(BlobObject(b"changed\n"))
    assert new_tree["dir/a.txt"][0] == changed_sha
    assert new_tree["dir/b.txt"] == old_tree["dir/b.txt"]
    assert new_tree["outside.txt"] == old_tree["outside.txt"]


def test_commit_only_paths_preserves_per_remote_server_options(tmp_path, monkeypatch):
    repo, files, blob_oids, _old_head = _partial_commit_repo(tmp_path)
    config = repo._read_config()
    config["remotes"]["origin"]["serverOption"] = ["trace=1", "feature=x"]
    repo._write_config(config)
    calls = []
    by_oid = {blob_oids[path]: data for path, data in files.items()}

    def fake_many(url, oids, *, server_options=()):
        calls.append((url, tuple(oids), tuple(server_options)))
        return {oid: NativeObject("blob", by_oid[oid], oid) for oid in oids}

    monkeypatch.setattr("pygit.promisor_materialize._fetch_native_objects", fake_many)
    (repo.worktree / "dir").mkdir(parents=True, exist_ok=True)
    (repo.worktree / "dir" / "a.txt").write_text("changed\n", encoding="utf-8")

    repo.commit(
        "change a",
        author_name="Test",
        author_email="test@example.com",
        only_paths=["dir/a.txt"],
    )

    assert len(calls) == 1
    assert calls[0][2] == ("trace=1", "feature=x")


def test_commit_without_only_paths_does_not_prefetch_promises(tmp_path, monkeypatch):
    repo, _files, blob_oids, old_head = _partial_commit_repo(tmp_path)
    monkeypatch.setattr(
        promisor_commit,
        "materialize_promised_objects",
        lambda *args, **kwargs: pytest.fail("ordinary commit must not prefetch HEAD promises"),
    )

    # Empty commit avoids consuming the unresolved HEAD tree while exercising
    # the public commit wrapper with no only_paths argument.
    new_head = repo.commit(
        "metadata only",
        author_name="Test",
        author_email="test@example.com",
        allow_empty=True,
        parents=[old_head],
    )

    assert new_head != old_head
    assert set(blob_oids.values()) <= set(read_promisor_state(repo.pygit_dir)["promised"])


def test_ordinary_commit_only_paths_keeps_historical_network_free_path(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "ordinary"))
    (repo.worktree / "a.txt").write_text("base\n", encoding="utf-8")
    (repo.worktree / "b.txt").write_text("base b\n", encoding="utf-8")
    repo.add(["a.txt", "b.txt"])
    repo.commit("initial", author_name="Test", author_email="test@example.com")

    (repo.worktree / "a.txt").write_text("changed\n", encoding="utf-8")
    monkeypatch.setattr(
        promisor_commit,
        "materialize_promised_objects",
        lambda *args, **kwargs: pytest.fail("ordinary repository must stay network-free"),
    )

    sha = repo.commit(
        "change a",
        author_name="Test",
        author_email="test@example.com",
        only_paths=["a.txt"],
    )
    tree = repo._commit_tree_entries(sha)
    assert set(tree) == {"a.txt", "b.txt"}
