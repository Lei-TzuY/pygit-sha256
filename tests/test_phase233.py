from __future__ import annotations

import hashlib

import pytest

from pygit.fetch_importer import PromisorFilteredNativeImporter
from pygit.promisor import read_promisor_state
from pygit.remote import NativeObject
from pygit.repo import Repository
from pygit.rev_list_disk_usage_cli import run_rev_list_disk_usage


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
        "\nallow promisor"
    ).encode()


def _partial_repo(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")
    blobs = {"a.txt": b"alpha\n", "b.txt": b"bravo\n"}
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


def test_rev_list_allow_promisor_omits_promises_without_fetching(tmp_path, monkeypatch, capsys):
    repo, local_commit, blob_oids = _partial_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_object",
        lambda *args, **kwargs: pytest.fail("allow-promisor must not fetch a promised object"),
    )
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_objects",
        lambda *args, **kwargs: pytest.fail("allow-promisor must not batch-fetch promised objects"),
    )

    before = read_promisor_state(repo.pygit_dir)
    capsys.readouterr()
    assert run_rev_list_disk_usage(["--objects", "--missing=allow-promisor", "HEAD"]) == 0
    after = read_promisor_state(repo.pygit_dir)

    lines = capsys.readouterr().out.splitlines()
    assert before == after
    assert lines[0] == local_commit
    assert len(lines) == 2
    root_tree = lines[1].split(" ", 1)[0]
    assert len(root_tree) == 64
    assert all(len(line.split(" ", 1)[0]) == 64 for line in lines)
    assert not any(native_oid in "\n".join(lines) for native_oid in blob_oids.values())


def test_rev_list_allow_promisor_no_object_names_is_sha256_only(tmp_path, monkeypatch, capsys):
    repo, _, _ = _partial_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        ["--objects", "--missing=allow-promisor", "--no-object-names", "HEAD"]
    ) == 0

    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2
    assert all(len(line) == 64 for line in lines)
    assert all(all(ch in "0123456789abcdef" for ch in line) for line in lines)


def test_rev_list_allow_promisor_ordinary_repo_matches_local_object_domain(tmp_path, monkeypatch, capsys):
    repo = Repository.init(str(tmp_path / "ordinary"))
    (repo.worktree / "a.txt").write_text("alpha\n", encoding="utf-8")
    repo.add(["a.txt"])
    commit = repo.commit("ordinary", author_name="Test", author_email="test@example.com")
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(["--objects", "--missing=allow-promisor", "HEAD"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == commit
    assert len(lines) == 3
    assert any(line.endswith(" a.txt") for line in lines)
    assert all(len(line.split(" ", 1)[0]) == 64 for line in lines)


def test_rev_list_allow_promisor_rejects_modes_not_yet_inventory_backed():
    with pytest.raises(ValueError, match="not yet supported"):
        run_rev_list_disk_usage(
            ["--objects", "--objects-edge", "--missing=allow-promisor", "HEAD"]
        )
