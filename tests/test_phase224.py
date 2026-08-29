from __future__ import annotations

import hashlib

import pytest

from pygit import promisor_three_way
from pygit.fetch_importer import PromisorFilteredNativeImporter
from pygit.objects import BlobObject
from pygit.promisor import read_promisor_state, update_promisor_state
from pygit.remote import NativeObject
from pygit.repo import Repository


def _native_oid(type_name: str, data: bytes) -> str:
    return hashlib.sha1(f"{type_name} {len(data)}\0".encode() + data).hexdigest()


def _tree_data(entries):
    return b"".join(
        b"100644 " + name.encode() + b"\x00" + bytes.fromhex(oid)
        for name, oid in sorted(entries.items())
    )


def _commit_data(tree_oid: str, message: str, parent: str | None = None) -> bytes:
    lines = [f"tree {tree_oid}\n"]
    if parent:
        lines.append(f"parent {parent}\n")
    lines.extend(
        [
            "author Test <test@example.com> 1 +0000\n",
            "committer Test <test@example.com> 1 +0000\n",
            f"\n{message}",
        ]
    )
    return "".join(lines).encode()


def _partial_divergence(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")

    blobs = {
        "base_a": b"base a\n",
        "base_b": b"base b\n",
        "ours_a": b"ours a\n",
        "theirs_b": b"theirs b\n",
    }
    blob_oids = {name: _native_oid("blob", data) for name, data in blobs.items()}

    base_tree_data = _tree_data(
        {
            "a.txt": blob_oids["base_a"],
            "b.txt": blob_oids["base_b"],
        }
    )
    ours_tree_data = _tree_data(
        {
            "a.txt": blob_oids["ours_a"],
            "b.txt": blob_oids["base_b"],
        }
    )
    theirs_tree_data = _tree_data(
        {
            "a.txt": blob_oids["base_a"],
            "b.txt": blob_oids["theirs_b"],
        }
    )
    base_tree_oid = _native_oid("tree", base_tree_data)
    ours_tree_oid = _native_oid("tree", ours_tree_data)
    theirs_tree_oid = _native_oid("tree", theirs_tree_data)

    base_commit_data = _commit_data(base_tree_oid, "base")
    base_commit_oid = _native_oid("commit", base_commit_data)
    ours_commit_data = _commit_data(ours_tree_oid, "ours", parent=base_commit_oid)
    ours_commit_oid = _native_oid("commit", ours_commit_data)
    theirs_commit_data = _commit_data(theirs_tree_oid, "theirs", parent=base_commit_oid)
    theirs_commit_oid = _native_oid("commit", theirs_commit_data)

    importer = PromisorFilteredNativeImporter(
        repo.store,
        {
            base_tree_oid: NativeObject("tree", base_tree_data, base_tree_oid),
            ours_tree_oid: NativeObject("tree", ours_tree_data, ours_tree_oid),
            theirs_tree_oid: NativeObject("tree", theirs_tree_data, theirs_tree_oid),
            base_commit_oid: NativeObject("commit", base_commit_data, base_commit_oid),
            ours_commit_oid: NativeObject("commit", ours_commit_data, ours_commit_oid),
            theirs_commit_oid: NativeObject("commit", theirs_commit_data, theirs_commit_oid),
        },
        remote="origin",
        filter_spec="blob:none",
    )
    base_local = importer.import_oid(base_commit_oid)
    ours_local = importer.import_oid(ours_commit_oid)
    theirs_local = importer.import_oid(theirs_commit_oid)

    # A realistic checked-out partial-clone HEAD already has the blobs needed by
    # its current worktree.  Historical/base and other-branch blobs remain
    # promised, which is exactly the three-way waterfall Phase224 addresses.
    ours_a_local = repo.store.write(BlobObject(blobs["ours_a"]))
    base_b_local = repo.store.write(BlobObject(blobs["base_b"]))
    update_promisor_state(
        repo.pygit_dir,
        resolved={
            blob_oids["ours_a"]: ours_a_local,
            blob_oids["base_b"]: base_b_local,
        },
    )

    repo.refs.set_branch("main", ours_local, message="test: ours")
    repo.refs.set_branch("feature", theirs_local, message="test: theirs")
    repo.refs.set_head_symbolic("main", message="test: main")
    repo._replace_worktree_from_commit(ours_local)

    return {
        "repo": repo,
        "blobs": blobs,
        "blob_oids": blob_oids,
        "base": base_local,
        "ours": ours_local,
        "theirs": theirs_local,
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
        lambda *args, **kwargs: pytest.fail("two unresolved three-way blobs must batch"),
    )


def test_non_fast_forward_merge_batches_base_ours_theirs_promises(tmp_path, monkeypatch):
    fx = _partial_divergence(tmp_path)
    repo = fx["repo"]
    repo.config_set("remote", "origin.serverOption", "trace=three-way")
    calls = []
    _install_bulk_fetch(monkeypatch, fx, calls)

    result = repo.merge(
        "feature",
        author_name="Test",
        author_email="test@example.com",
    )

    assert result["status"] == "merged"
    assert len(calls) == 1
    assert calls[0][0] == "https://example.test/repo.git"
    assert set(calls[0][1]) == {
        fx["blob_oids"]["base_a"],
        fx["blob_oids"]["theirs_b"],
    }
    assert calls[0][2] == ("trace=three-way",)
    assert (repo.worktree / "a.txt").read_bytes() == fx["blobs"]["ours_a"]
    assert (repo.worktree / "b.txt").read_bytes() == fx["blobs"]["theirs_b"]

    state = read_promisor_state(repo.pygit_dir)
    assert fx["blob_oids"]["base_a"] in state["resolved"]
    assert fx["blob_oids"]["theirs_b"] in state["resolved"]


def test_cherry_pick_uses_one_shared_three_way_batch(tmp_path, monkeypatch):
    fx = _partial_divergence(tmp_path)
    repo = fx["repo"]
    calls = []
    _install_bulk_fetch(monkeypatch, fx, calls)

    result = repo.cherry_pick(
        "feature",
        committer_name="Test",
        committer_email="test@example.com",
    )

    assert result["status"] == "picked"
    assert len(calls) == 1
    assert set(calls[0][1]) == {
        fx["blob_oids"]["base_a"],
        fx["blob_oids"]["theirs_b"],
    }
    assert (repo.worktree / "a.txt").read_bytes() == fx["blobs"]["ours_a"]
    assert (repo.worktree / "b.txt").read_bytes() == fx["blobs"]["theirs_b"]


def test_merge_prefetch_failure_is_before_head_index_or_worktree_mutation(
    tmp_path, monkeypatch
):
    fx = _partial_divergence(tmp_path)
    repo = fx["repo"]
    before_head = repo.refs.resolve_head()
    before_index = {
        path: (entry.sha, entry.mode)
        for path, entry in repo.index.entries.items()
    }
    before_worktree = {
        "a.txt": (repo.worktree / "a.txt").read_bytes(),
        "b.txt": (repo.worktree / "b.txt").read_bytes(),
    }

    monkeypatch.setattr(
        promisor_three_way,
        "materialize_promised_objects",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("promisor offline")),
    )

    with pytest.raises(RuntimeError, match="promisor offline"):
        repo.merge("feature")

    assert repo.refs.resolve_head() == before_head
    assert {
        path: (entry.sha, entry.mode)
        for path, entry in repo.index.entries.items()
    } == before_index
    assert (repo.worktree / "a.txt").read_bytes() == before_worktree["a.txt"]
    assert (repo.worktree / "b.txt").read_bytes() == before_worktree["b.txt"]
    assert repo._read_merge_head() is None


def _ordinary_divergence(tmp_path):
    repo = Repository.init(str(tmp_path / "ordinary"))
    (repo.worktree / "a.txt").write_text("base a\n", encoding="utf-8")
    (repo.worktree / "b.txt").write_text("base b\n", encoding="utf-8")
    repo.add(["a.txt", "b.txt"])
    base = repo.commit("base", author_name="Test", author_email="test@example.com")
    repo.branch("feature")

    (repo.worktree / "a.txt").write_text("ours a\n", encoding="utf-8")
    repo.add(["a.txt"])
    ours = repo.commit("ours", author_name="Test", author_email="test@example.com")

    repo.checkout("feature")
    (repo.worktree / "b.txt").write_text("theirs b\n", encoding="utf-8")
    repo.add(["b.txt"])
    theirs = repo.commit("theirs", author_name="Test", author_email="test@example.com")
    repo.checkout("main")
    return repo, base, ours, theirs


def test_rebase_replay_routes_through_shared_three_way_prefetch(tmp_path, monkeypatch):
    repo, base, ours, theirs = _ordinary_divergence(tmp_path)
    seen = []

    def record_prefetch(_repo, commit_shas):
        seen.append(tuple(commit_shas))
        return set()

    monkeypatch.setattr(promisor_three_way, "prefetch_commit_promises", record_prefetch)

    result = repo.rebase(
        "feature",
        committer_name="Test",
        committer_email="test@example.com",
    )

    assert result["status"] == "rebased"
    assert seen == [(base, theirs, ours)]
    assert (repo.worktree / "a.txt").read_text(encoding="utf-8") == "ours a\n"
    assert (repo.worktree / "b.txt").read_text(encoding="utf-8") == "theirs b\n"


def test_ordinary_three_way_merge_does_not_materialize_promises(tmp_path, monkeypatch):
    repo, _base, _ours, _theirs = _ordinary_divergence(tmp_path)
    monkeypatch.setattr(
        promisor_three_way,
        "materialize_promised_objects",
        lambda *args, **kwargs: pytest.fail("ordinary merge must stay network-free"),
    )

    result = repo.merge(
        "feature",
        author_name="Test",
        author_email="test@example.com",
    )
    assert result["status"] == "merged"
