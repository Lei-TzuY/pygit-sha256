from __future__ import annotations

import hashlib

from pygit.clone_shallow import clone_shallow_repository
from pygit.fetch_importer import StableShallowNativeImporter
from pygit.fetch_shallow import read_shallow
from pygit.foreign_commits import read_foreign_commit_map
from pygit.objects import CommitObject
from pygit.protocol_v2_fetch import SmartHttpV2FetchClient, V2FetchResult
from pygit.remote import Advertisement, NativeObject
from pygit.repo import Repository


def _native_oid(type_name: str, data: bytes) -> str:
    return hashlib.sha1(f"{type_name} {len(data)}\0".encode() + data).hexdigest()


def _tree_and_blob(text: bytes, name: str = "a.txt"):
    blob_oid = _native_oid("blob", text)
    tree_data = b"100644 " + name.encode() + b"\x00" + bytes.fromhex(blob_oid)
    tree_oid = _native_oid("tree", tree_data)
    return (
        blob_oid,
        NativeObject("blob", text, blob_oid),
        tree_oid,
        NativeObject("tree", tree_data, tree_oid),
    )


def _commit(tree_oid: str, message: str, parent: str | None = None):
    lines = [f"tree {tree_oid}"]
    if parent is not None:
        lines.append(f"parent {parent}")
    lines.extend(
        [
            "author Dev <dev@example.com> 0 +0000",
            "committer Dev <dev@example.com> 0 +0000",
            "",
            message,
        ]
    )
    data = "\n".join(lines).encode()
    oid = _native_oid("commit", data)
    return oid, NativeObject("commit", data, oid)


def test_importer_accepts_missing_native_parent_and_keeps_stable_sha(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    parent_blob_oid, parent_blob, parent_tree_oid, parent_tree = _tree_and_blob(b"parent\n")
    parent_oid, parent_commit = _commit(parent_tree_oid, "parent")

    child_blob_oid, child_blob, child_tree_oid, child_tree = _tree_and_blob(b"child\n")
    child_oid, child_commit = _commit(child_tree_oid, "child", parent=parent_oid)
    child_objects = {
        child_blob_oid: child_blob,
        child_tree_oid: child_tree,
        child_oid: child_commit,
    }

    child_sha = StableShallowNativeImporter(repo.store, child_objects).import_oid(child_oid)
    child_before = repo.store.read(child_sha)
    assert isinstance(child_before, CommitObject)
    assert child_before.native_parents == [parent_oid]
    assert child_before.parents == []

    parent_objects = {
        parent_blob_oid: parent_blob,
        parent_tree_oid: parent_tree,
        parent_oid: parent_commit,
    }
    parent_sha = StableShallowNativeImporter(repo.store, parent_objects).import_oid(parent_oid)

    child_after = repo.store.read(child_sha)
    assert isinstance(child_after, CommitObject)
    assert child_after.parents == [parent_sha]
    assert repo.store.write(child_after) == child_sha


def test_foreign_commit_map_survives_repository_reopen(tmp_path):
    path = tmp_path / "repo"
    repo = Repository.init(str(path))
    blob_oid, blob, tree_oid, tree = _tree_and_blob(b"root\n")
    commit_oid, commit = _commit(tree_oid, "root")
    local_sha = StableShallowNativeImporter(
        repo.store,
        {blob_oid: blob, tree_oid: tree, commit_oid: commit},
    ).import_oid(commit_oid)

    assert read_foreign_commit_map(repo.pygit_dir)[commit_oid] == local_sha
    reopened = Repository(str(path))
    loaded = reopened.store.read(local_sha)
    assert isinstance(loaded, CommitObject)
    assert loaded.message == "root"


def test_true_shallow_clone_imports_pack_without_boundary_parent(tmp_path, monkeypatch):
    parent_blob_oid, parent_blob, parent_tree_oid, parent_tree = _tree_and_blob(b"parent\n")
    parent_oid, _parent_commit = _commit(parent_tree_oid, "parent")
    top_blob_oid, top_blob, top_tree_oid, top_tree = _tree_and_blob(b"top\n")
    top_oid, top_commit = _commit(top_tree_oid, "top", parent=parent_oid)

    advertisement = Advertisement(
        refs={"HEAD": top_oid, "refs/heads/main": top_oid},
        capabilities={"fetch=shallow", "ls-refs"},
        symrefs={"HEAD": "refs/heads/main"},
    )
    truncated_objects = {
        top_blob_oid: top_blob,
        top_tree_oid: top_tree,
        top_oid: top_commit,
    }
    calls = []

    monkeypatch.setattr(
        SmartHttpV2FetchClient,
        "discover_refs",
        lambda self: advertisement,
    )

    def fake_fetch(self, haves=None, advertisement=None, **kwargs):
        calls.append((list(haves or []), dict(advertisement.refs), dict(kwargs)))
        return V2FetchResult(
            advertisement,
            truncated_objects,
            shallow=(top_oid,),
            unshallow=(),
        )

    monkeypatch.setattr(SmartHttpV2FetchClient, "fetch", fake_fetch)

    repo = clone_shallow_repository(
        "https://example.test/repo.git",
        str(tmp_path / "clone"),
        depth=1,
        branch_name=None,
        single_branch=True,
    )

    assert calls == [
        (
            [],
            {"refs/heads/main": top_oid},
            {"deepen": 1},
        )
    ]
    top_sha = repo.refs.get_remote("origin", "main")
    assert top_sha is not None
    top = repo.store.read(top_sha)
    assert isinstance(top, CommitObject)
    assert top.native_parents == [parent_oid]
    assert top.parents == []
    assert read_shallow(repo) == {top_sha}
    assert repo.refs.resolve_head() == top_sha
    assert repo.config_get("protocol", "version") == "2"
    assert not repo.store.exists("0" * 64)
    # The omitted native parent was never converted or stored locally.
    assert parent_oid not in read_foreign_commit_map(repo.pygit_dir)


def test_no_single_branch_shallow_clone_fetches_all_branch_tips(tmp_path, monkeypatch):
    blob_oid, blob, tree_oid, tree = _tree_and_blob(b"same\n")
    main_oid, main_commit = _commit(tree_oid, "main")
    dev_oid, dev_commit = _commit(tree_oid, "dev")
    advertisement = Advertisement(
        refs={
            "HEAD": main_oid,
            "refs/heads/main": main_oid,
            "refs/heads/dev": dev_oid,
        },
        capabilities={"fetch=shallow", "ls-refs"},
        symrefs={"HEAD": "refs/heads/main"},
    )
    objects = {
        blob_oid: blob,
        tree_oid: tree,
        main_oid: main_commit,
        dev_oid: dev_commit,
    }
    seen = {}

    monkeypatch.setattr(SmartHttpV2FetchClient, "discover_refs", lambda self: advertisement)

    def fake_fetch(self, haves=None, advertisement=None, **kwargs):
        seen["refs"] = dict(advertisement.refs)
        return V2FetchResult(advertisement, objects, shallow=(), unshallow=())

    monkeypatch.setattr(SmartHttpV2FetchClient, "fetch", fake_fetch)

    repo = clone_shallow_repository(
        "https://example.test/repo.git",
        str(tmp_path / "clone"),
        depth=5,
        branch_name=None,
        single_branch=False,
    )

    assert set(seen["refs"]) == {"refs/heads/main", "refs/heads/dev"}
    assert repo.refs.get_remote("origin", "main") is not None
    assert repo.refs.get_remote("origin", "dev") is not None
