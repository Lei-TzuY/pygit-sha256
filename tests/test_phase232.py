from __future__ import annotations

import hashlib

import pytest

from pygit.fetch_importer import PromisorFilteredNativeImporter
from pygit.promisor import read_promisor_state
from pygit.promisor_object_inventory import promisor_object_inventory
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
        "\npartial inventory"
    ).encode()


def _partial_repo(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")

    blobs = {
        "a.txt": b"alpha\n",
        "b.txt": b"bravo\n",
        "c.txt": b"charlie\n",
    }
    blob_oids = {name: _native_oid("blob", data) for name, data in blobs.items()}
    tree_data = _tree_data(blob_oids)
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
    return repo, local_commit, blob_oids


def test_inventory_reports_promises_without_fetching_or_faking_sha256(tmp_path, monkeypatch):
    repo, local_commit, blob_oids = _partial_repo(tmp_path)

    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_object",
        lambda *args, **kwargs: pytest.fail("inventory must not fault promised blobs in"),
    )
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_objects",
        lambda *args, **kwargs: pytest.fail("inventory must not batch-fetch promised blobs"),
    )

    before = read_promisor_state(repo.pygit_dir)
    entries = promisor_object_inventory(repo, ("HEAD",))
    after = read_promisor_state(repo.pygit_dir)

    assert before == after
    assert entries[0].type_name == "commit"
    assert entries[0].oid == local_commit
    assert len(entries[0].oid) == 64
    assert entries[0].native_oid is None

    trees = [entry for entry in entries if entry.type_name == "tree"]
    missing = [entry for entry in entries if entry.missing]
    assert len(trees) == 1
    assert len(trees[0].oid) == 64
    assert trees[0].native_oid is None
    assert {entry.path for entry in missing} == set(blob_oids)
    assert {entry.native_oid for entry in missing} == set(blob_oids.values())
    assert all(entry.oid is None for entry in missing)
    assert all(len(entry.native_oid or "") == 40 for entry in missing)
    assert all(entry.type_name == "blob" for entry in missing)


def test_inventory_ordinary_repository_stays_entirely_sha256_native(tmp_path):
    repo = Repository.init(str(tmp_path / "ordinary"))
    (repo.worktree / "a.txt").write_text("alpha\n", encoding="utf-8")
    (repo.worktree / "b.txt").write_text("bravo\n", encoding="utf-8")
    repo.add(["a.txt", "b.txt"])
    commit = repo.commit("ordinary", author_name="Test", author_email="test@example.com")

    entries = promisor_object_inventory(repo, ("HEAD",))

    assert entries[0].oid == commit
    assert {entry.path for entry in entries if entry.type_name == "blob"} == {"a.txt", "b.txt"}
    assert all(not entry.missing for entry in entries)
    assert all(entry.native_oid is None for entry in entries)
    assert all(entry.oid is not None and len(entry.oid) == 64 for entry in entries)


def test_inventory_subtracts_negative_revision_object_closure(tmp_path):
    repo = Repository.init(str(tmp_path / "range"))
    (repo.worktree / "shared.txt").write_text("shared\n", encoding="utf-8")
    repo.add(["shared.txt"])
    base = repo.commit("base", author_name="Test", author_email="test@example.com")

    (repo.worktree / "new.txt").write_text("new\n", encoding="utf-8")
    repo.add(["new.txt"])
    tip = repo.commit("tip", author_name="Test", author_email="test@example.com")

    entries = promisor_object_inventory(repo, (f"{base}..{tip}",))

    assert [entry.oid for entry in entries if entry.type_name == "commit"] == [tip]
    paths = {entry.path for entry in entries if entry.type_name == "blob"}
    assert "new.txt" in paths
    assert "shared.txt" not in paths
    assert all(entry.native_oid is None for entry in entries)
