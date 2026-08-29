from __future__ import annotations

import hashlib
from typing import Optional

import pytest

from pygit import promisor_blame
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


def _commit_data(tree_oid: str, message: str, parent: Optional[str] = None) -> bytes:
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


def _partial_blame_repo(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")

    blobs = {
        "root_a": b"old\nkeep\n",
        "child_a": b"new\nkeep\n",
        "common_b": b"unrelated\n",
    }
    blob_oids = {name: _native_oid("blob", data) for name, data in blobs.items()}

    root_tree_data = _tree_data(
        {"a.txt": blob_oids["root_a"], "b.txt": blob_oids["common_b"]}
    )
    child_tree_data = _tree_data(
        {"a.txt": blob_oids["child_a"], "b.txt": blob_oids["common_b"]}
    )
    root_tree_oid = _native_oid("tree", root_tree_data)
    child_tree_oid = _native_oid("tree", child_tree_data)

    root_commit_data = _commit_data(root_tree_oid, "root")
    root_commit_oid = _native_oid("commit", root_commit_data)
    child_commit_data = _commit_data(child_tree_oid, "child", parent=root_commit_oid)
    child_commit_oid = _native_oid("commit", child_commit_data)

    importer = PromisorFilteredNativeImporter(
        repo.store,
        {
            root_tree_oid: NativeObject("tree", root_tree_data, root_tree_oid),
            child_tree_oid: NativeObject("tree", child_tree_data, child_tree_oid),
            root_commit_oid: NativeObject("commit", root_commit_data, root_commit_oid),
            child_commit_oid: NativeObject("commit", child_commit_data, child_commit_oid),
        },
        remote="origin",
        filter_spec="blob:none",
    )
    root_local = importer.import_oid(root_commit_oid)
    child_local = importer.import_oid(child_commit_oid)
    repo.refs.set_branch("main", child_local, message="test: child")
    repo.refs.set_head_symbolic("main", message="test: main")

    # Blame displays the current worktree contents. Populate only that file
    # directly so the fixture does not accidentally materialize promises through
    # checkout before the blame wrapper gets a chance to batch them.
    (repo.worktree / "a.txt").write_bytes(blobs["child_a"])

    return repo, blobs, blob_oids, root_local, child_local


def _install_bulk_fetch(monkeypatch, blobs, blob_oids, calls):
    by_oid = {blob_oids[name]: data for name, data in blobs.items()}

    def fake_many(url, oids, *, server_options=()):
        wanted = tuple(oids)
        calls.append((url, wanted, tuple(server_options)))
        return {oid: NativeObject("blob", by_oid[oid], oid) for oid in wanted}

    monkeypatch.setattr("pygit.promisor_materialize._fetch_native_objects", fake_many)
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_object",
        lambda *args, **kwargs: pytest.fail("blame history promises must batch"),
    )


def test_blame_batches_complete_history_snapshot_promises(tmp_path, monkeypatch):
    repo, blobs, blob_oids, root, child = _partial_blame_repo(tmp_path)
    repo.config_set("remote", "origin.serverOption", "trace=blame")
    calls = []
    _install_bulk_fetch(monkeypatch, blobs, blob_oids, calls)

    lines = repo.blame("a.txt")

    assert len(calls) == 1
    assert calls[0][0] == "https://example.test/repo.git"
    assert set(calls[0][1]) == set(blob_oids.values())
    assert calls[0][2] == ("trace=blame",)
    assert lines[0].startswith(child[:12])
    assert lines[0].endswith("new")
    assert lines[1].startswith(root[:12])
    assert lines[1].endswith("keep")

    state = read_promisor_state(repo.pygit_dir)
    assert set(blob_oids.values()).issubset(state["resolved"])
    assert not (set(blob_oids.values()) & set(state["promised"]))


def test_blame_line_range_keeps_one_bulk_prefetch_and_output_slice(tmp_path, monkeypatch):
    repo, blobs, blob_oids, root, _child = _partial_blame_repo(tmp_path)
    calls = []
    _install_bulk_fetch(monkeypatch, blobs, blob_oids, calls)

    lines = repo.blame("a.txt", line_range=(2, 2))

    assert len(calls) == 1
    assert set(calls[0][1]) == set(blob_oids.values())
    assert len(lines) == 1
    assert lines[0].startswith(root[:12])
    assert lines[0].endswith("keep")


def test_missing_worktree_path_preserves_error_before_network(tmp_path, monkeypatch):
    repo, _blobs, _blob_oids, _root, _child = _partial_blame_repo(tmp_path)
    (repo.worktree / "a.txt").unlink()
    monkeypatch.setattr(
        promisor_blame,
        "prefetch_history_promises",
        lambda *args, **kwargs: pytest.fail("missing worktree path must not prefetch"),
    )

    with pytest.raises(FileNotFoundError, match="missing.txt"):
        repo.blame("missing.txt")


def test_empty_repository_preserves_no_commits_error_before_network(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "empty"))
    # A sidecar promise can exist independently of commit history; the wrapper
    # must still preserve blame's empty-history error without a network attempt.
    from pygit.promisor import update_promisor_state

    update_promisor_state(
        repo.pygit_dir,
        remote="origin",
        filter_spec="blob:none",
        promised={"1" * 40: "blob"},
    )
    monkeypatch.setattr(
        promisor_blame,
        "prefetch_history_promises",
        lambda *args, **kwargs: pytest.fail("empty history must not prefetch"),
    )

    with pytest.raises(RuntimeError, match="No commits found"):
        repo.blame("a.txt")


def test_ordinary_blame_stays_network_free(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "ordinary"))
    (repo.worktree / "a.txt").write_text("old\nkeep\n", encoding="utf-8")
    repo.add(["a.txt"])
    root = repo.commit("root", author_name="Test", author_email="test@example.com")
    (repo.worktree / "a.txt").write_text("new\nkeep\n", encoding="utf-8")
    repo.add(["a.txt"])
    child = repo.commit("child", author_name="Test", author_email="test@example.com")

    monkeypatch.setattr(
        promisor_blame,
        "prefetch_history_promises",
        lambda *args, **kwargs: pytest.fail("ordinary blame must stay network-free"),
    )

    lines = repo.blame("a.txt")
    assert lines[0].startswith(child[:12])
    assert lines[1].startswith(root[:12])
