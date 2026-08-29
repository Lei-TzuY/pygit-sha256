from __future__ import annotations

import hashlib

import pytest

from pygit.fetch_importer import PromisorFilteredNativeImporter
from pygit.fsck import fsck
from pygit.promisor import read_promisor_state, write_promisor_state
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
        "\npartial fsck"
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


def _forbid_fetch(monkeypatch):
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_object",
        lambda *args, **kwargs: pytest.fail("fsck must not fault promised blobs in"),
    )
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_objects",
        lambda *args, **kwargs: pytest.fail("fsck must not batch-fetch promised blobs"),
    )


def test_full_fsck_accepts_expected_promisor_missing_without_fetch(tmp_path, monkeypatch):
    repo, local_commit, blob_oids = _partial_repo(tmp_path)
    _forbid_fetch(monkeypatch)

    before = read_promisor_state(repo.pygit_dir)
    report = fsck(repo, include_index=False)
    after = read_promisor_state(repo.pygit_dir)

    assert report.ok
    assert not report.errors
    assert before == after
    assert local_commit in report.reachable
    assert all(len(oid) == 64 for oid in report.checked_objects)
    assert set(blob_oids.values()).issubset(before["promised"])
    assert set(blob_oids.values()).isdisjoint(report.checked_objects)


def test_connectivity_only_skips_unresolved_promises_without_fetch(tmp_path, monkeypatch):
    repo, local_commit, blob_oids = _partial_repo(tmp_path)
    _forbid_fetch(monkeypatch)

    before = read_promisor_state(repo.pygit_dir)
    report = fsck(repo, connectivity_only=True, include_index=False)
    after = read_promisor_state(repo.pygit_dir)

    assert report.ok
    assert before == after
    assert local_commit in report.reachable
    assert all(len(oid) == 64 for oid in report.reachable)
    assert set(blob_oids.values()).isdisjoint(report.reachable)


def test_fsck_rejects_unrecorded_native_missing_object_without_fetch(tmp_path, monkeypatch):
    repo, _, blob_oids = _partial_repo(tmp_path)
    _forbid_fetch(monkeypatch)

    victim = blob_oids["b.txt"]
    state = read_promisor_state(repo.pygit_dir)
    state["promised"].pop(victim)
    write_promisor_state(repo.pygit_dir, state)

    report = fsck(repo, include_index=False)

    assert not report.ok
    issues = [issue for issue in report.errors if issue.code == "missing-promisor-object"]
    assert len(issues) == 1
    assert victim in issues[0].message


def test_fsck_rejects_promisor_kind_mismatch_without_fetch(tmp_path, monkeypatch):
    repo, _, blob_oids = _partial_repo(tmp_path)
    _forbid_fetch(monkeypatch)

    victim = blob_oids["a.txt"]
    state = read_promisor_state(repo.pygit_dir)
    state["promised"][victim] = "tree"
    write_promisor_state(repo.pygit_dir, state)

    report = fsck(repo, include_index=False)

    assert not report.ok
    issues = [issue for issue in report.errors if issue.code == "wrong-promisor-type"]
    assert len(issues) == 1
    assert victim in issues[0].message


def test_ordinary_repository_keeps_existing_fsck_semantics(tmp_path):
    repo = Repository.init(str(tmp_path / "ordinary"))
    (repo.worktree / "a.txt").write_text("alpha\n", encoding="utf-8")
    repo.add(["a.txt"])
    commit = repo.commit("ordinary", author_name="Test", author_email="test@example.com")

    report = fsck(repo)

    assert report.ok
    assert commit in report.reachable
    assert all(len(oid) == 64 for oid in report.checked_objects)
