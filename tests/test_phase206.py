"""Phase206 regressions for stable shallow export and clone tag auto-follow."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

from pygit import Repository
from pygit.clone_shallow import (
    _eligible_auto_follow_tags,
    clone_shallow_repository,
)
from pygit.fetch_importer import StableShallowNativeImporter
from pygit.objects import CommitObject, TagObject
from pygit.remote import Advertisement, NativeExporter, NativeObject
from pygit.store import ObjectStore


def _native_oid(type_name: str, data: bytes) -> str:
    return hashlib.sha1(f"{type_name} {len(data)}\0".encode() + data).hexdigest()


def _native_tree(blob_oid: str, name: str = "hello.txt") -> bytes:
    return b"100644 " + name.encode() + b"\x00" + bytes.fromhex(blob_oid)


def _native_commit(tree_oid: str, *, parent: str | None, message: str) -> bytes:
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
    return "\n".join(lines).encode()


def _native_tag(target_oid: str, name: str = "v1") -> bytes:
    return (
        f"object {target_oid}\n"
        "type commit\n"
        f"tag {name}\n"
        "tagger Dev <dev@example.com> 0 +0000\n"
        "\n"
        f"release {name}"
    ).encode()


def test_truncated_foreign_commit_exports_original_native_parent(tmp_path):
    blob_data = b"hello\n"
    blob_oid = _native_oid("blob", blob_data)
    tree_data = _native_tree(blob_oid)
    tree_oid = _native_oid("tree", tree_data)
    missing_parent = "a" * 40
    child_data = _native_commit(tree_oid, parent=missing_parent, message="child")
    child_oid = _native_oid("commit", child_data)

    objects = {
        blob_oid: NativeObject("blob", blob_data, blob_oid),
        tree_oid: NativeObject("tree", tree_data, tree_oid),
        child_oid: NativeObject("commit", child_data, child_oid),
    }
    store = ObjectStore(tmp_path / ".pygit" / "objects")
    importer = StableShallowNativeImporter(store, objects)
    child_local = importer.import_oid(child_oid)

    child = store.read(child_local)
    assert isinstance(child, CommitObject)
    assert child.parents == []
    assert child.native_parents == [missing_parent]

    exporter = NativeExporter(store)
    exported_oid = exporter.export_oid(child_local)

    assert exported_oid == child_oid
    assert exporter.objects[child_oid].data == child_data
    assert missing_parent not in exporter.objects


def test_deepened_foreign_commit_keeps_native_oid_and_exports_parent(tmp_path):
    blob_data = b"hello\n"
    blob_oid = _native_oid("blob", blob_data)
    tree_data = _native_tree(blob_oid)
    tree_oid = _native_oid("tree", tree_data)

    parent_data = _native_commit(tree_oid, parent=None, message="parent")
    parent_oid = _native_oid("commit", parent_data)
    child_data = _native_commit(tree_oid, parent=parent_oid, message="child")
    child_oid = _native_oid("commit", child_data)

    store = ObjectStore(tmp_path / ".pygit" / "objects")
    initial = {
        blob_oid: NativeObject("blob", blob_data, blob_oid),
        tree_oid: NativeObject("tree", tree_data, tree_oid),
        child_oid: NativeObject("commit", child_data, child_oid),
    }
    first = StableShallowNativeImporter(store, initial)
    child_local = first.import_oid(child_oid)
    original_local = child_local

    second = StableShallowNativeImporter(
        store,
        {parent_oid: NativeObject("commit", parent_data, parent_oid)},
        known=first.converted,
    )
    parent_local = second.import_oid(parent_oid)

    child = store.read(child_local)
    assert isinstance(child, CommitObject)
    assert child.parents == [parent_local]
    assert child_local == original_local

    exporter = NativeExporter(store)
    exported_oid = exporter.export_oid(child_local)

    assert exported_oid == child_oid
    assert child_oid in exporter.objects
    assert parent_oid in exporter.objects
    assert exporter.objects[child_oid].data == child_data
    assert exporter.objects[parent_oid].data == parent_data


def test_auto_follow_selection_requires_known_peeled_target():
    reachable = "1" * 40
    unreachable = "2" * 40
    annotated = "3" * 40
    deep_tag = "4" * 40
    advertisement = Advertisement(
        refs={
            "refs/heads/main": reachable,
            "refs/tags/light": reachable,
            "refs/tags/v1": annotated,
            "refs/tags/v1^{}": reachable,
            "refs/tags/deep": deep_tag,
            "refs/tags/deep^{}": unreachable,
        },
        capabilities=set(),
        symrefs={"HEAD": "refs/heads/main"},
    )

    selected = _eligible_auto_follow_tags(
        advertisement,
        {reachable: "a" * 64},
    )

    assert selected == {
        "refs/tags/light": reachable,
        "refs/tags/v1": annotated,
    }


def test_true_shallow_clone_auto_follows_only_reachable_tags(monkeypatch, tmp_path):
    blob_data = b"hello\n"
    blob_oid = _native_oid("blob", blob_data)
    tree_data = _native_tree(blob_oid)
    tree_oid = _native_oid("tree", tree_data)

    missing_parent = "9" * 40
    child_data = _native_commit(tree_oid, parent=missing_parent, message="child")
    child_oid = _native_oid("commit", child_data)

    tag_data = _native_tag(child_oid, "v1")
    tag_oid = _native_oid("tag", tag_data)
    deep_target = "8" * 40
    deep_tag_data = _native_tag(deep_target, "deep")
    deep_tag_oid = _native_oid("tag", deep_tag_data)

    advertisement = Advertisement(
        refs={
            "refs/heads/main": child_oid,
            "refs/tags/light": child_oid,
            "refs/tags/v1": tag_oid,
            "refs/tags/v1^{}": child_oid,
            "refs/tags/deep": deep_tag_oid,
            "refs/tags/deep^{}": deep_target,
        },
        capabilities={"fetch=shallow", "ls-refs", "object-format=sha1"},
        symrefs={"HEAD": "refs/heads/main"},
    )
    initial_objects = {
        blob_oid: NativeObject("blob", blob_data, blob_oid),
        tree_oid: NativeObject("tree", tree_data, tree_oid),
        child_oid: NativeObject("commit", child_data, child_oid),
    }
    initial_result = SimpleNamespace(
        advertisement=advertisement,
        objects=initial_objects,
        shallow=(child_oid,),
        unshallow=(),
    )
    tag_result = SimpleNamespace(
        advertisement=advertisement,
        objects={tag_oid: NativeObject("tag", tag_data, tag_oid)},
        shallow=(),
        unshallow=(),
    )
    calls = []

    class FakeClient:
        def __init__(self, url):
            self.url = url

        def discover_refs(self):
            return advertisement

        def fetch(self, haves=None, advertisement=None, **kwargs):
            calls.append((list(haves or []), dict(advertisement.refs), dict(kwargs)))
            if kwargs.get("deepen") is not None:
                assert kwargs["deepen"] == 1
                assert advertisement.refs == {"refs/heads/main": child_oid}
                return initial_result
            assert advertisement.refs == {"refs/tags/v1": tag_oid}
            assert child_oid in set(haves or [])
            assert tuple(kwargs.get("shallow", ())) == (child_oid,)
            return tag_result

    monkeypatch.setattr("pygit.clone_shallow.SmartHttpV2FetchClient", FakeClient)

    repo = clone_shallow_repository(
        "https://example.test/repo.git",
        str(tmp_path / "clone"),
        depth=1,
        branch_name=None,
        single_branch=True,
    )

    branch_sha = repo.refs.get_remote("origin", "main")
    assert branch_sha is not None
    assert repo.refs.get_tag("light") == branch_sha

    annotated_sha = repo.refs.get_tag("v1")
    assert annotated_sha is not None
    annotated_obj = repo.store.read(annotated_sha)
    assert isinstance(annotated_obj, TagObject)
    assert annotated_obj.target_sha == branch_sha
    assert repo.refs.get_tag("deep") is None

    assert (repo.pygit_dir / "shallow").read_text(encoding="utf-8").strip() == branch_sha
    native_map = repo._read_native_map("origin")
    assert native_map[branch_sha] == child_oid
    assert native_map[annotated_sha] == tag_oid
    assert len(calls) == 2
